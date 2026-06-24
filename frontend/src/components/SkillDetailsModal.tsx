/**
 * SkillDetailsModal — shows the SKILL.md instructions for an installed skill.
 * Opened via "Ver" button on each skill row.
 */

import { createPortal } from 'react-dom'
import { useEffect, useRef } from 'react'
import type { SkillDetails } from '../api/types'
import Badge from './Badge'

interface SkillDetailsModalProps {
  details: SkillDetails
  onClose(): void
}

export default function SkillDetailsModal({ details, onClose }: SkillDetailsModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeBtnRef = useRef<HTMLButtonElement>(null)

  const titleId = 'skill-details-modal-title'

  useEffect(() => {
    closeBtnRef.current?.focus()
  }, [])

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
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
  }, [onClose])

  const name = details.skill_name ?? details.package_id
  const version = details.version ? `v${details.version}` : ''
  const state = details.state ?? ''

  return createPortal(
    <div
      className="mfa-modal-backdrop"
      role="presentation"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="skill-details-modal"
      >
        <div className="mfa-modal__header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', flex: 1, minWidth: 0 }}>
            <h2 id={titleId} className="mfa-modal__title" style={{ flex: 1, minWidth: 0 }}>
              {name}
            </h2>
            {version && <Badge variant="neutral">{version}</Badge>}
            {state && <Badge variant="accent">{state}</Badge>}
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            className="mfa-modal__close"
            aria-label="Cerrar"
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        <div className="skill-details-modal__body">
          {details.instructions !== null ? (
            <pre className="skill-details-modal__instructions">
              {details.instructions}
            </pre>
          ) : (
            <p className="skill-details-modal__no-instructions">
              Esta skill no tiene instrucciones en disco (p. ej. Composio).
            </p>
          )}
        </div>

        <div className="mfa-modal__actions" style={{ padding: 'var(--sp-4) var(--sp-6)', borderTop: '1px solid var(--line)' }}>
          <button
            type="button"
            className="cv-btn cv-btn--secondary cv-btn--sm"
            onClick={onClose}
          >
            Cerrar
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
