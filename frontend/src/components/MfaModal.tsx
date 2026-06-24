/**
 * MfaModal — reusable modal that collects MFA factors before a sensitive action.
 *
 * Tiers:
 *   'mfa'           — TOTP only (6-digit code)
 *   'mfa_humanity'  — TOTP + in-client math challenge (proves human presence)
 *   'mfa_riddle'    — TOTP + personal riddle answer
 *
 * The parent is responsible for making the API call; this modal only
 * collects and validates inputs client-side, then calls onSign with the factors.
 * Errors from the API should be surfaced via sileo toasts by the parent.
 */

import { createPortal } from 'react-dom'
import { useEffect, useRef, useState } from 'react'
import { sileo } from 'sileo'

export type MfaTier = 'mfa' | 'mfa_humanity' | 'mfa_riddle'

export interface MfaFactors {
  totp: string
  humanity?: string
  riddle_answer?: string
}

export interface MfaModalProps {
  tier: MfaTier
  title: string
  /** For mfa_riddle: the question text fetched from GET /mfa/status */
  riddleQuestion?: string
  /** Called when the user submits valid inputs. Parent fires the API call. */
  onSign(factors: MfaFactors): void
  onCancel(): void
}

function generateChallenge(): { a: number; b: number } {
  return {
    a: Math.floor(Math.random() * 10) + 1,
    b: Math.floor(Math.random() * 10) + 1,
  }
}

export default function MfaModal({
  tier,
  title,
  riddleQuestion,
  onSign,
  onCancel,
}: MfaModalProps) {
  const [totp, setTotp] = useState('')
  const [riddleAnswer, setRiddleAnswer] = useState('')
  const [humanityInput, setHumanityInput] = useState('')
  const [challenge] = useState(generateChallenge)
  const totpRef = useRef<HTMLInputElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  const needsHumanity = tier === 'mfa_humanity'
  const needsRiddle = tier === 'mfa_riddle'
  const expectedSum = challenge.a + challenge.b

  // Focus TOTP on open
  useEffect(() => {
    totpRef.current?.focus()
  }, [])

  // Escape closes; Tab stays inside
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onCancel()
        return
      }
      if (e.key === 'Tab') {
        const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
          'button, input, [tabindex]:not([tabindex="-1"])',
        )
        if (!focusable || focusable.length === 0) return
        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        if (e.shiftKey) {
          if (document.activeElement === first) { e.preventDefault(); last.focus() }
        } else {
          if (document.activeElement === last) { e.preventDefault(); first.focus() }
        }
      }
    }
    document.addEventListener('keydown', handleKey, true)
    return () => document.removeEventListener('keydown', handleKey, true)
  }, [onCancel])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()

    if (!totp.trim()) {
      sileo.error({ title: 'Introduce tu código MFA de 6 dígitos.' })
      totpRef.current?.focus()
      return
    }

    if (needsHumanity) {
      const parsed = parseInt(humanityInput.trim(), 10)
      if (isNaN(parsed) || parsed !== expectedSum) {
        sileo.error({ title: `Respuesta incorrecta. Resuelve: ${challenge.a} + ${challenge.b}` })
        return
      }
    }

    if (needsRiddle && !riddleAnswer.trim()) {
      sileo.error({ title: 'Introduce la respuesta a tu acertijo.' })
      return
    }

    const factors: MfaFactors = { totp: totp.trim() }
    if (needsHumanity) factors.humanity = humanityInput.trim()
    if (needsRiddle) factors.riddle_answer = riddleAnswer.trim()

    onSign(factors)
  }

  const modalId = 'mfa-modal'
  const titleId = 'mfa-modal-title'

  return createPortal(
    <div
      className="mfa-modal-backdrop"
      role="presentation"
      onClick={e => { if (e.target === e.currentTarget) onCancel() }}
    >
      <div
        id={modalId}
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="mfa-modal"
      >
        <div className="mfa-modal__header">
          <h2 id={titleId} className="mfa-modal__title">{title}</h2>
          <button
            type="button"
            className="mfa-modal__close"
            aria-label="Cerrar"
            onClick={onCancel}
          >
            ✕
          </button>
        </div>

        <form className="mfa-modal__body" onSubmit={handleSubmit}>
          <div className="mfa-modal__field">
            <label htmlFor="mfa-modal-totp" className="cv-label">
              Código MFA
            </label>
            <input
              id="mfa-modal-totp"
              ref={totpRef}
              className="cv-input"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={8}
              placeholder="6 dígitos"
              aria-label="Código MFA de 6 dígitos"
              value={totp}
              onChange={e => setTotp(e.target.value)}
            />
          </div>

          {needsHumanity && (
            <div className="mfa-modal__field">
              <label htmlFor="mfa-modal-humanity" className="cv-label">
                Prueba de presencia — Resuelve: {challenge.a} + {challenge.b} =
              </label>
              <input
                id="mfa-modal-humanity"
                className="cv-input"
                inputMode="numeric"
                placeholder={`Resultado de ${challenge.a} + ${challenge.b}`}
                aria-label={`Resultado de ${challenge.a} más ${challenge.b}`}
                value={humanityInput}
                onChange={e => setHumanityInput(e.target.value)}
              />
            </div>
          )}

          {needsRiddle && (
            <div className="mfa-modal__field">
              <label htmlFor="mfa-modal-riddle" className="cv-label">
                {riddleQuestion ? `Acertijo: ${riddleQuestion}` : 'Respuesta de tu acertijo personal'}
              </label>
              <input
                id="mfa-modal-riddle"
                className="cv-input"
                type="text"
                placeholder="Tu respuesta"
                aria-label="Respuesta al acertijo personal"
                value={riddleAnswer}
                onChange={e => setRiddleAnswer(e.target.value)}
              />
            </div>
          )}

          <div className="mfa-modal__actions">
            <button
              type="button"
              className="cv-btn cv-btn--ghost cv-btn--sm"
              onClick={onCancel}
            >
              Cancelar
            </button>
            <button
              type="submit"
              className="cv-btn cv-btn--primary cv-btn--sm"
            >
              Firmar
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
