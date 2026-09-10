import { act } from 'react-dom/test-utils'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import React from 'react'

// No @testing-library in this project yet — render directly via react-dom
// (mirrors TailnetSection.test.tsx).

// Regression (matriz 10-sep, hallazgo C): the emergency-brake release dialog
// always asked for a TOTP, even when MFA was never enrolled — a dead end
// (403 mfa_not_enrolled, no path forward). Pins the fixed UI: the release
// dialog shows WHICH proof it's asking for, based on GET /mfa/status.

const { getKillSwitch, engageKillSwitch, releaseKillSwitch, mfaStatus, sileoSuccess, sileoError } =
  vi.hoisted(() => ({
    getKillSwitch: vi.fn(),
    engageKillSwitch: vi.fn(),
    releaseKillSwitch: vi.fn(),
    mfaStatus: vi.fn(),
    sileoSuccess: vi.fn(),
    sileoError: vi.fn(),
  }))

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return { ...actual, getKillSwitch, engageKillSwitch, releaseKillSwitch, mfaStatus }
})
vi.mock('sileo', () => ({ sileo: { success: sileoSuccess, error: sileoError } }))

import { KillSwitchSection } from './SeguridadView'
import type { KillSwitchStatus } from '../api/types'

const ENGAGED: KillSwitchStatus = {
  engaged: true, reason: 'freno de prueba', changed_by: 'owner', changed_at: '2026-09-10T10:00:00Z',
}

function clickButton(container: ParentNode, matcher: (text: string) => boolean) {
  const button = Array.from(container.querySelectorAll('button')).find(
    b => matcher(b.textContent ?? ''),
  )
  if (!button) throw new Error('button not found')
  act(() => { button.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
}

function typeInto(input: HTMLInputElement, value: string) {
  const nativeSetter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  )!.set!
  act(() => {
    nativeSetter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

async function flush() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe('KillSwitchSection — release dialog shows which proof is asked', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    getKillSwitch.mockReset()
    engageKillSwitch.mockReset()
    releaseKillSwitch.mockReset()
    mfaStatus.mockReset()
    sileoSuccess.mockReset()
    sileoError.mockReset()
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => { root.unmount() })
    container.remove()
    document.body.querySelectorAll('.mfa-modal-backdrop').forEach(el => el.remove())
  })

  it('MFA not enrolled: release button + dialog ask for the device password, not TOTP', async () => {
    getKillSwitch.mockResolvedValue(ENGAGED)
    mfaStatus.mockResolvedValue({ enrolled: false })
    releaseKillSwitch.mockResolvedValue({ ok: true })

    act(() => { root.render(React.createElement(KillSwitchSection)) })
    await flush()

    expect(container.textContent).toContain('contraseña del dispositivo')
    clickButton(container, t => t.includes('Liberar'))
    await flush()

    const passwordInput = document.body.querySelector<HTMLInputElement>('.mfa-modal input[type="password"]')
    expect(passwordInput).not.toBeNull()
    expect(document.body.querySelector('input[inputmode="numeric"]')).toBeNull()

    typeInto(passwordInput!, 'mi-contraseña-del-dispositivo')
    clickButton(document.body, t => t === 'Confirmar con contraseña')
    await flush()

    expect(releaseKillSwitch).toHaveBeenCalledWith({ devicePassword: 'mi-contraseña-del-dispositivo' })
    expect(sileoSuccess).toHaveBeenCalledTimes(1)
  })

  it('MFA enrolled: release button + dialog ask for a TOTP code', async () => {
    getKillSwitch.mockResolvedValue(ENGAGED)
    mfaStatus.mockResolvedValue({ enrolled: true })
    releaseKillSwitch.mockResolvedValue({ ok: true })

    act(() => { root.render(React.createElement(KillSwitchSection)) })
    await flush()

    expect(container.textContent).toContain('TOTP')
    clickButton(container, t => t.includes('Liberar'))
    await flush()

    const totpInput = document.body.querySelector<HTMLInputElement>('.mfa-modal input[inputmode="numeric"]')
    expect(totpInput).not.toBeNull()
    expect(document.body.querySelector('.mfa-modal input[type="password"]')).toBeNull()

    typeInto(totpInput!, '123456')
    clickButton(document.body, t => t.includes('Confirmar con código'))
    await flush()

    expect(releaseKillSwitch).toHaveBeenCalledWith({ totp: '123456' })
    expect(sileoSuccess).toHaveBeenCalledTimes(1)
  })
})
