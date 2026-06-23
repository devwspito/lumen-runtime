/**
 * ApprovalCard — HITL approval widget.
 *
 * Rendered both inside SeguridadView (full list) and PendingApprovalsInChat
 * (filtered to the active conversation). The card is self-contained: it
 * handles deny, the tiered MFA/humanity/riddle form, and the "no MFA enrolled"
 * inline recovery flow.
 */

import { useRef, useState } from 'react'
import { sileo } from 'sileo'
import { resolveApproval } from '../api/client'
import type { PendingApproval } from '../api/types'
import MfaEnroll from './MfaEnroll'

// Map backend error codes / messages to user-friendly strings.
function mapApproveError(err: unknown): { userMessage: string; isNotEnrolled: boolean } {
  const raw = err instanceof Error ? err.message : String(err)
  const lower = raw.toLowerCase()

  if (lower.includes('mfa_not_enrolled') || lower.includes('not enrolled')) {
    return { userMessage: '', isNotEnrolled: true }
  }
  if (lower.includes('invalid_totp') || lower.includes('invalid totp')) {
    return { userMessage: 'Código incorrecto o caducado — genera uno nuevo en tu app.', isNotEnrolled: false }
  }
  if (lower.includes('mfa_denied') || lower.includes('mfa denied')) {
    return { userMessage: 'Verificación denegada — comprueba tu código e inténtalo de nuevo.', isNotEnrolled: false }
  }
  if (lower.includes('invalid_riddle') || lower.includes('riddle')) {
    return { userMessage: 'Respuesta al acertijo incorrecta.', isNotEnrolled: false }
  }
  return { userMessage: raw, isNotEnrolled: false }
}

export interface ApprovalCardProps {
  approval: PendingApproval
  onResolved(): void
}

export default function ApprovalCard({ approval, onResolved }: ApprovalCardProps) {
  const [showMfa, setShowMfa] = useState(false)
  const [totp, setTotp] = useState('')
  const [riddle, setRiddle] = useState('')
  const [humanity, setHumanity] = useState(false)
  const [busy, setBusy] = useState(false)
  const [errorMessage, setErrorMessage] = useState('')
  // True when either the approval.mfa_enrolled flag says so, or the backend
  // returned mfa_not_enrolled. Triggers the inline MfaEnroll recovery flow.
  const [needsEnroll, setNeedsEnroll] = useState(approval.mfa_enrolled === false)
  const totpRef = useRef<HTMLInputElement>(null)

  const params = approval.parameters
  const paramEntries =
    params && typeof params === 'object' && !Array.isArray(params)
      ? Object.entries(params).slice(0, 8)
      : []

  // Tier flags derived from required_level
  const level = approval.required_level ?? 'mfa'
  const needsHumanity = level === 'mfa_humanity' || level === 'mfa_riddle'
  const needsRiddle = level === 'mfa_riddle'

  // If riddle tier is required but no riddle is configured, warn inline.
  const riddleNotReady = needsRiddle && approval.riddle_set === false

  async function handleDeny() {
    setBusy(true)
    try {
      await resolveApproval(approval.proposal_id, 'deny')
      sileo.success({ title: 'Denegado' })
      onResolved()
    } catch (err) {
      sileo.error({ title: `No se pudo denegar: ${err instanceof Error ? err.message : err}` })
    } finally {
      setBusy(false)
    }
  }

  function openMfa() {
    setShowMfa(true)
    setTimeout(() => totpRef.current?.focus(), 50)
  }

  async function handleApprove(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErrorMessage('')
    try {
      await resolveApproval(approval.proposal_id, 'once', {
        totp: totp.trim() || null,
        riddle_answer: needsRiddle ? (riddle.trim() || null) : null,
        humanity: needsHumanity ? (humanity ? 'confirmado' : null) : null,
      })
      sileo.success({ title: 'Aprobado' })
      onResolved()
    } catch (err) {
      const { userMessage, isNotEnrolled } = mapApproveError(err)
      if (isNotEnrolled) {
        setNeedsEnroll(true)
      } else {
        setErrorMessage(userMessage)
        sileo.error({ title: userMessage || 'No se pudo aprobar' })
      }
    } finally {
      setBusy(false)
    }
  }

  function handleEnrolled() {
    // MFA is now set up; go back to the code form for this same card.
    setNeedsEnroll(false)
    setShowMfa(true)
    setTimeout(() => totpRef.current?.focus(), 50)
  }

  return (
    <div
      className="seg-approval-card"
      role="alertdialog"
      aria-label={`Aprobación requerida: ${approval.summary}`}
    >
      <div className="seg-approval-card__body">
        <p className="seg-approval-card__question">{approval.summary}</p>
        {approval.target && (
          <p className="seg-approval-card__target">{approval.target}</p>
        )}
        {paramEntries.length > 0 && (
          <dl className="seg-approval-card__params">
            {paramEntries.map(([k, v]) => (
              <div key={k} className="seg-approval-card__param-row">
                <dt>{k}</dt>
                <dd>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
              </div>
            ))}
          </dl>
        )}
        {riddleNotReady && (
          <p
            className="seg-approval-card__mfa-error"
            role="alert"
          >
            Esta acción requiere un acertijo personal que aún no has configurado.{' '}
            <a href="/seguridad" style={{ color: 'var(--accent)' }}>
              Configúralo en Seguridad
            </a>
            .
          </p>
        )}
      </div>

      {/* ── Inline MFA enrollment recovery ── */}
      {needsEnroll ? (
        <div className="seg-approval-card__mfa">
          <p
            className="seg-approval-card__mfa-error"
            role="status"
            style={{ color: 'var(--warn)' }}
          >
            Necesitas configurar tu verificación (MFA) para aprobar esto.
          </p>
          <MfaEnroll onEnrolled={handleEnrolled} />
        </div>
      ) : (
        <>
          {/* ── Primary actions ── */}
          <div
            className="seg-approval-card__actions"
            role="group"
            aria-label="Acciones de aprobación"
          >
            <button
              className="cv-btn cv-btn--primary cv-btn--sm"
              onClick={openMfa}
              disabled={busy || riddleNotReady}
              type="button"
              aria-label="Permitir esta acción (requiere tu MFA)"
            >
              Permitir…
            </button>
            <button
              className="cv-btn cv-btn--ghost cv-btn--sm"
              onClick={handleDeny}
              disabled={busy}
              type="button"
              aria-label="Denegar esta acción"
            >
              Denegar
            </button>
          </div>

          {/* ── MFA verification form ── */}
          {showMfa && (
            <form
              className="seg-approval-card__mfa"
              onSubmit={handleApprove}
              aria-label="Verificación del dueño"
            >
              <input
                ref={totpRef}
                className="cv-input"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={8}
                placeholder="Código MFA (6 dígitos)"
                aria-label="Código MFA"
                value={totp}
                onChange={e => setTotp(e.target.value)}
              />

              {needsRiddle && (
                <input
                  className="cv-input"
                  type="text"
                  placeholder="Respuesta de tu acertijo personal"
                  aria-label="Respuesta del acertijo"
                  value={riddle}
                  onChange={e => setRiddle(e.target.value)}
                />
              )}

              {needsHumanity && (
                <label className="seg-approval-card__humanity">
                  <input
                    type="checkbox"
                    checked={humanity}
                    onChange={e => setHumanity(e.target.checked)}
                  />
                  Confirmo que soy yo (presencia humana)
                </label>
              )}

              {errorMessage && (
                <p
                  className="seg-approval-card__mfa-error"
                  role="alert"
                  aria-live="assertive"
                >
                  {errorMessage}
                </p>
              )}

              <div className="seg-approval-card__mfa-actions">
                <button
                  type="submit"
                  className="cv-btn cv-btn--primary cv-btn--sm"
                  disabled={busy}
                >
                  {busy ? 'Confirmando…' : 'Confirmar'}
                </button>
                <button
                  type="button"
                  className="cv-btn cv-btn--ghost cv-btn--sm"
                  onClick={() => { setShowMfa(false); setErrorMessage('') }}
                  disabled={busy}
                >
                  Cancelar
                </button>
              </div>
            </form>
          )}
        </>
      )}
    </div>
  )
}
