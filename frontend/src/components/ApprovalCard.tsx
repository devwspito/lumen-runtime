/**
 * ApprovalCard — HITL approval widget.
 *
 * Rendered both inside SeguridadView (full list) and PendingApprovalsInChat
 * (filtered to the active conversation).
 *
 * "Permitir" opens MfaModal (unless MFA is disabled — then fires directly).
 * "Denegar" fires without MFA.
 * Errors surface as toasts; the modal stays open on failure.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { sileo } from 'sileo'
import { resolveApproval } from '../api/client'
import type { PendingApproval } from '../api/types'
import MfaEnroll from './MfaEnroll'
import MfaModal from './MfaModal'
import type { MfaTier, MfaFactors } from './MfaModal'

export interface ApprovalCardProps {
  approval: PendingApproval
  /** When true, "Permitir" bypasses MFA entirely (mfa_on_dangers is OFF). */
  mfaDisabled?: boolean
  onResolved(): void
}

export default function ApprovalCard({
  approval,
  mfaDisabled = false,
  onResolved,
}: ApprovalCardProps) {
  const navigate = useNavigate()
  const [showModal, setShowModal] = useState(false)
  const [busy, setBusy] = useState(false)
  // Triggers inline enrollment if backend says MFA not enrolled
  const [needsEnroll, setNeedsEnroll] = useState(approval.mfa_enrolled === false)

  const params = approval.parameters
  const paramEntries =
    params && typeof params === 'object' && !Array.isArray(params)
      ? Object.entries(params).slice(0, 8)
      : []

  const level = approval.required_level ?? 'mfa'
  const tier: MfaTier =
    level === 'mfa_riddle' ? 'mfa_riddle'
    : level === 'mfa_humanity' ? 'mfa_humanity'
    : 'mfa'

  const riddleNotReady = tier === 'mfa_riddle' && approval.riddle_set === false

  async function handleDeny() {
    setBusy(true)
    try {
      await resolveApproval(approval.proposal_id, 'deny')
      sileo.success({ title: 'Acción denegada' })
      onResolved()
    } catch (err) {
      sileo.error({ title: `No se pudo denegar: ${err instanceof Error ? err.message : err}` })
    } finally {
      setBusy(false)
    }
  }

  function handlePermitirClick() {
    if (mfaDisabled) {
      // MFA globally off — approve directly without factors
      void handleApprove({} as MfaFactors)
    } else {
      setShowModal(true)
    }
  }

  async function handleApprove(factors: MfaFactors) {
    setBusy(true)
    try {
      await resolveApproval(approval.proposal_id, 'once', {
        totp: factors.totp ?? null,
        humanity: factors.humanity ?? null,
        riddle_answer: factors.riddle_answer ?? null,
      })
      setShowModal(false)
      sileo.success({ title: 'Acción aprobada' })
      onResolved()
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      const lower = msg.toLowerCase()
      if (lower.includes('mfa_not_enrolled') || lower.includes('not enrolled')) {
        setShowModal(false)
        setNeedsEnroll(true)
      } else {
        // Toast the error; the modal stays open so user can retry
        sileo.error({ title: msg || 'No se pudo aprobar' })
      }
    } finally {
      setBusy(false)
    }
  }

  function handleEnrolled() {
    setNeedsEnroll(false)
    // Re-open the modal for the now-enrolled user
    setShowModal(true)
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
          <p className="seg-approval-card__mfa-error" role="alert">
            Esta acción requiere un acertijo personal que aún no has configurado.{' '}
            <button
              type="button"
              onClick={() => navigate('/seguridad')}
              style={{
                background: 'none',
                border: 'none',
                padding: 0,
                color: 'var(--accent)',
                cursor: 'pointer',
                fontSize: 'inherit',
                textDecoration: 'underline',
              }}
            >
              Configúralo en Seguridad
            </button>
            .
          </p>
        )}
      </div>

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
        <div
          className="seg-approval-card__actions"
          role="group"
          aria-label="Acciones de aprobación"
        >
          <button
            className="cv-btn cv-btn--primary cv-btn--sm"
            onClick={handlePermitirClick}
            disabled={busy || riddleNotReady}
            type="button"
            aria-label={
              mfaDisabled
                ? 'Permitir esta acción'
                : 'Permitir esta acción (requiere tu MFA)'
            }
          >
            Permitir
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
      )}

      {showModal && (
        <MfaModal
          tier={tier}
          title="Aprobar acción"
          onSign={handleApprove}
          onCancel={() => setShowModal(false)}
        />
      )}
    </div>
  )
}
