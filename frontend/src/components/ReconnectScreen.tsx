/**
 * ReconnectScreen — the ONE honest state for a tokenless load, a stale
 * cached bearer, or a refresh that definitively failed (028 FR-012/FR-013,
 * SC-012). Rendered by App.tsx INSTEAD OF the whole routed app shell, so
 * nothing that polls or fetches ever mounts alongside it — no error stack,
 * no retry loop, one screen, one action.
 */
import { useT } from '../lib/i18n'
import type { AuthStatus } from '../lib/token'

export interface ReconnectScreenProps {
  reason: Extract<AuthStatus, { kind: 'unauthenticated' }>['reason']
}

export function ReconnectScreen({ reason }: ReconnectScreenProps) {
  const t = useT()

  return (
    <div
      role="alert"
      aria-live="assertive"
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--space-3)',
        minHeight: '100vh',
        padding: 'var(--space-6)',
        textAlign: 'center',
      }}
    >
      <h1 style={{ fontSize: 'var(--text-xl, 1.25rem)', margin: 0 }}>
        {t('reconnect.title')}
      </h1>
      <p style={{ color: 'var(--color-text-muted)', margin: 0, maxWidth: '32em' }}>
        {t(`reconnect.reason.${reason}`)}
      </p>
      <p style={{ color: 'var(--color-text-muted)', margin: 0, maxWidth: '32em' }}>
        {t('reconnect.hint')}
      </p>
      <button
        type="button"
        className="cv-btn cv-btn--primary"
        style={{ marginTop: 'var(--space-2)' }}
        onClick={() => window.location.reload()}
      >
        {t('reconnect.action')}
      </button>
    </div>
  )
}
