import { act } from 'react-dom/test-utils'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import React from 'react'

// No @testing-library in this project yet — render directly via react-dom
// (mirrors TailnetSection.test.tsx / InboundDelegationCard.test.tsx).

// Regression (matriz 10-sep, hallazgo A): the skills hub `force` install was
// unreachable from the UI — approving a FAIL scan spent the owner's ONE TOTP
// on POST /security/decisions, then the install retry demanded a SECOND
// code, which would fail anyway (TOTP is single-use, totp_replayed). Pins
// the fixed flow: exactly ONE recordSecurityDecision (one TOTP) → installSkill
// is called with the re-auth grant it returns, not a second totp.

const {
  listSkills, searchSkillsHub, listHubSkills, installSkill, getHubOpStatus,
  uninstallHubSkill, promoteSkill, getSkillDetails, scanInstall, recordSecurityDecision,
  sileoSuccess, sileoError, sileoWarning,
} = vi.hoisted(() => ({
  listSkills: vi.fn(),
  searchSkillsHub: vi.fn(),
  listHubSkills: vi.fn(),
  installSkill: vi.fn(),
  getHubOpStatus: vi.fn(),
  uninstallHubSkill: vi.fn(),
  promoteSkill: vi.fn(),
  getSkillDetails: vi.fn(),
  scanInstall: vi.fn(),
  recordSecurityDecision: vi.fn(),
  sileoSuccess: vi.fn(),
  sileoError: vi.fn(),
  sileoWarning: vi.fn(),
}))

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    listSkills, searchSkillsHub, listHubSkills, installSkill, getHubOpStatus,
    uninstallHubSkill, promoteSkill, getSkillDetails, scanInstall, recordSecurityDecision,
  }
})
vi.mock('sileo', () => ({ sileo: { success: sileoSuccess, error: sileoError, warning: sileoWarning } }))
vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  useOutletContext: () => ({ startNew: vi.fn(), sendMessage: vi.fn() }),
}))

import SkillsView from './SkillsView'
import type { HubSkillResult, InstallScanResponse } from '../api/types'

const RESULT: HubSkillResult = {
  identifier: 'official/research/gitnexus-explorer',
  name: 'gitnexus-explorer',
  source: 'clawhub',
}

const FAIL_SCAN: InstallScanResponse = {
  scan_id: 'scan-1',
  verdict: 'FAIL',
  score: 30,
  engine: 'heuristic',
  engine_label: 'heuristic',
  requires_owner_approval: true,
  risks: [{ category: 'network', severity: 'HIGH', message: 'contacta un host desconocido' }],
}

function clickButton(container: HTMLElement, matcher: (text: string) => boolean) {
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

describe('SkillsView — hub install force (owner MFA)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    for (const fn of [
      listSkills, searchSkillsHub, listHubSkills, installSkill, getHubOpStatus,
      uninstallHubSkill, promoteSkill, getSkillDetails, scanInstall, recordSecurityDecision,
      sileoSuccess, sileoError, sileoWarning,
    ]) fn.mockReset()

    listSkills.mockResolvedValue([])
    listHubSkills.mockResolvedValue([])
    searchSkillsHub.mockResolvedValue({ results: [RESULT] })
    scanInstall.mockResolvedValue(FAIL_SCAN)
    recordSecurityDecision.mockResolvedValue({ ok: true, reauth_grant: 'grant-abc123' })
    installSkill.mockResolvedValue({ op_id: 'op-1', status: 'pending' })

    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => { root.unmount() })
    container.remove()
  })

  it('sends exactly ONE TOTP: approving a FAIL scan installs via the re-auth grant, no second code', async () => {
    act(() => { root.render(React.createElement(SkillsView)) })
    await flush()

    const searchInput = container.querySelector<HTMLInputElement>('#hub-search')
    if (!searchInput) throw new Error('search input not found')
    typeInto(searchInput, 'gitnexus')
    clickButton(container, t => t === 'Buscar')
    await flush()

    clickButton(container, t => t === 'Instalar')
    await flush()

    expect(scanInstall).toHaveBeenCalledWith('skill', 'official/research/gitnexus-explorer')
    // InstallScanModal + MfaModal render via createPortal(document.body) —
    // outside `container` — so scope those lookups to document.body.
    clickButton(document.body, t => t === 'Aprobar e instalar')
    await flush()

    // MfaModal is now up — type the owner's TOTP and confirm.
    const totpInput = document.body.querySelector<HTMLInputElement>('.mfa-modal input')
    if (!totpInput) throw new Error('TOTP input not found')
    typeInto(totpInput, '123456')
    clickButton(document.body, t => t === 'Confirmar con código')
    await flush()

    // Exactly one TOTP left the browser, on /security/decisions.
    expect(recordSecurityDecision).toHaveBeenCalledTimes(1)
    expect(recordSecurityDecision).toHaveBeenCalledWith(
      expect.objectContaining({ totp: '123456', identifier: 'official/research/gitnexus-explorer' }),
    )

    // The install retry carries force=true but NO second totp — and it's the
    // ONLY installSkill call.
    expect(installSkill).toHaveBeenCalledTimes(1)
    expect(installSkill).toHaveBeenCalledWith(
      'official/research/gitnexus-explorer',
      true,
      'grant-abc123',
    )
  })
})
