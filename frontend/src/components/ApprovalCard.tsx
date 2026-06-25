/**
 * ApprovalCard — HITL approval widget.
 *
 * Rendered both inside SeguridadView (full list) and PendingApprovalsInChat
 * (filtered to the active conversation).
 *
 * Escalated MFA model (owner decision 2026-06-25):
 *   - simple tier (required_level === "simple"): Aprobar/Denegar directly — no TOTP.
 *     Applies to most tools: cronjob, send_message, delegate_task, browser actions, etc.
 *   - mfa tier  (required_level === "mfa"):  MfaModal is shown before approving.
 *     Applies to cage-widening tools: install_*, set_policy, disable_mfa, skill_manage,
 *     and any _DESTRUCTIVE tools.
 *
 * Classification is server-side (tool_delicacy.is_mfa_required). The client
 * reads `approval.required_level` — never does its own word-list scan.
 */

import { useState } from 'react'
import { sileo } from 'sileo'
import { resolveApproval } from '../api/client'
import type { PendingApproval } from '../api/types'
import MfaModal from './MfaModal'

export interface ApprovalCardProps {
  approval: PendingApproval
  /** Legacy compat — no longer the gate; classification comes from required_level. */
  mfaDisabled?: boolean
  onResolved(): void
}

export default function ApprovalCard({
  approval,
  onResolved,
}: ApprovalCardProps) {
  const [busy, setBusy] = useState(false)
  const [showMfaModal, setShowMfaModal] = useState(false)

  const isMfaTier = approval.required_level === 'mfa'

  const params = approval.parameters
  const paramEntries =
    params && typeof params === 'object' && !Array.isArray(params)
      ? Object.entries(params).slice(0, 8)
      : []

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

  function handleApproveClick() {
    if (isMfaTier) {
      // mfa-tier: open MfaModal to collect TOTP before sending approve.
      setShowMfaModal(true)
    } else {
      // simple-tier: approve immediately, no TOTP.
      void doApprove()
    }
  }

  async function doApprove(totp?: string) {
    setBusy(true)
    try {
      await resolveApproval(approval.proposal_id, 'once', { totp: totp ?? null })
      sileo.success({ title: 'Acción aprobada' })
      onResolved()
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      sileo.error({ title: msg || 'No se pudo aprobar' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
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
        </div>

        <div
          className="seg-approval-card__actions"
          role="group"
          aria-label="Acciones de aprobación"
        >
          <button
            className="cv-btn cv-btn--primary cv-btn--sm"
            onClick={handleApproveClick}
            disabled={busy}
            type="button"
            aria-label={isMfaTier ? 'Permitir esta acción (requiere MFA)' : 'Permitir esta acción'}
          >
            {isMfaTier ? 'Permitir (MFA)' : 'Permitir'}
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
      </div>

      {showMfaModal && (
        <MfaModal
          title={`Autorizar: ${approval.target || approval.summary}`}
          onSign={({ totp }) => {
            setShowMfaModal(false)
            void doApprove(totp)
          }}
          onCancel={() => setShowMfaModal(false)}
        />
      )}
    </>
  )
}
