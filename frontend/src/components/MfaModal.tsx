/**
 * MfaModal — collects a TOTP code before a sensitive action.
 *
 * The parent fires the API call; this modal only collects and validates
 * the TOTP input, then calls onSign. API errors should be surfaced via
 * sileo toasts by the parent.
 */

import { createPortal } from 'react-dom'
import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { sileo } from 'sileo'

export type MfaTier = 'mfa'

export interface MfaFactors {
  totp: string
}

export interface MfaModalProps {
  title: string
  onSign(factors: MfaFactors): void
  onCancel(): void
}

export default function MfaModal({ title, onSign, onCancel }: MfaModalProps) {
  const [totp, setTotp] = useState('')
  const totpRef = useRef<HTMLInputElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    totpRef.current?.focus()
  }, [])

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
    onSign({ totp: totp.trim() })
  }

  const titleId = 'mfa-modal-title'

  return createPortal(
    <div
      className="mfa-modal-backdrop"
      role="presentation"
      onClick={e => { if (e.target === e.currentTarget) onCancel() }}
    >
      <div
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
            <X size={16} aria-hidden="true" />
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
