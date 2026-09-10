/**
 * DevicePasswordModal — collects the owner's DEVICE password before a
 * sensitive action, for flows where MFA isn't enrolled (025 hallazgo C: the
 * emergency-brake release used to have TOTP as its only proof — a dead end
 * when the owner never set up MFA). Same interaction contract as MfaModal
 * (same markup/CSS classes, different input), verified server-side via the
 * SAME PAM root-helper path POST /tailnet/disconnect uses.
 *
 * Interaction contract:
 *   - Calls onSign when a non-empty password is submitted; the parent drives
 *     the API call and decides when to close.
 *   - Closes (onCancel) on Escape / backdrop click / Cancel button.
 */

import { createPortal } from 'react-dom'
import { useId, useRef, useState, useEffect } from 'react'
import { X } from 'lucide-react'

export interface DevicePasswordModalProps {
  title: string
  /** Explains WHICH proof is being asked and why (025: "MFA no está
   * configurado — usa la contraseña de tu dispositivo para continuar"). */
  description: string
  onSign(password: string): void
  onCancel(): void
}

export default function DevicePasswordModal({
  title, description, onSign, onCancel,
}: DevicePasswordModalProps) {
  const [password, setPassword] = useState('')
  const [inlineError, setInlineError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  const titleId = useId()
  const descId = useId()
  const errorId = useId()

  useEffect(() => {
    inputRef.current?.focus()
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
    const value = password
    if (!value) {
      setInlineError('Introduce la contraseña de tu dispositivo.')
      inputRef.current?.focus()
      return
    }
    setInlineError('')
    onSign(value)
  }

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
        aria-describedby={descId}
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
          <p id={descId} className="mfa-modal__inline-error" style={{ color: 'inherit' }}>
            {description}
          </p>
          <div className="mfa-modal__field">
            <label htmlFor={`${titleId}-password`} className="cv-label">
              Contraseña del dispositivo
            </label>
            <input
              id={`${titleId}-password`}
              ref={inputRef}
              className="cv-input"
              type="password"
              autoComplete="current-password"
              placeholder="Contraseña del dispositivo"
              aria-label="Contraseña del dispositivo"
              aria-describedby={inlineError ? errorId : undefined}
              aria-invalid={!!inlineError}
              value={password}
              onChange={e => {
                setPassword(e.target.value)
                if (inlineError) setInlineError('')
              }}
            />
            {inlineError && (
              <p id={errorId} role="alert" className="mfa-modal__inline-error">
                {inlineError}
              </p>
            )}
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
              Confirmar con contraseña
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
