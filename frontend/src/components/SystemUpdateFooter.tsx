/**
 * SystemUpdateFooter — sidebar footer: current version, "Actualizar" (only
 * when a newer version is REALLY confirmed — contracts/update.md §3, 028
 * FR-015/SC-006), and "Desinstalar". A single click drops an install-request
 * (verb `update_system`) and the footer shows honest, named-stage progress
 * read back from that same request until the app relaunches on its own.
 */
import { useCallback, useEffect, useState } from 'react'
import { sileo } from 'sileo'
import { RefreshCw, Trash2 } from 'lucide-react'
import {
  getSystemUpdate,
  requestSystemUninstall,
  postInstallRequest,
  getInstallRequests,
  type SystemUpdateStatus,
} from '../api/client'
import type { InstallRequestStatus, SafentUpdateGlobal } from '../api/types'
import { useConfirmDialog } from './ConfirmDialog'
import { useT } from '../lib/i18n'
import { stageLabelKey, formatProgress } from '../lib/installStages'

const SYSTEM_UPDATE_POLL_MS = 15 * 60_000
// While an update is in flight, poll fast: the owner is WATCHING "Updating…"
// and must see completion (or the stale-flag expiry) in seconds, not in 15 min.
const SYSTEM_UPDATE_ACTIVE_POLL_MS = 20_000

/** Compare two dotted versions ("0.8.34"). >0 if a is newer than b, 0 if equal, <0 older. */
function cmpVersion(a: string, b: string): number {
  const pa = a.split('.').map(n => parseInt(n, 10) || 0)
  const pb = b.split('.').map(n => parseInt(n, 10) || 0)
  const len = Math.max(pa.length, pb.length)
  for (let i = 0; i < len; i++) {
    const d = (pa[i] || 0) - (pb[i] || 0)
    if (d !== 0) return d > 0 ? 1 : -1
  }
  return 0
}

function injectedUpdate(): SafentUpdateGlobal | undefined {
  if (typeof window === 'undefined') return undefined
  return (window as unknown as { __safentUpdate?: SafentUpdateGlobal }).__safentUpdate
}

/** Older host shells only ever injected a bare version string — kept as a fallback
 *  (expandir → contraer) until every shell ships the richer contracts/update.md object. */
function legacyInjectedVersion(): string {
  if (typeof window === 'undefined') return ''
  const v = (window as unknown as { __safentLatestVersion?: unknown }).__safentLatestVersion
  return typeof v === 'string' ? v.trim() : ''
}

interface UpdateSignal {
  /** "is_newer" — a newer version is REALLY confirmed, from at least one source that could check. */
  available: boolean
  latestVersion: string
}

/** The Tauri host shell (has real internet) and the daemon (whose egress cage can
 *  block its own check) each compute this independently — the UI trusts whichever
 *  source actually managed to check (contracts/update.md §3 "dos fuentes, una verdad"). */
function resolveUpdateSignal(status: SystemUpdateStatus): UpdateSignal {
  const host = injectedUpdate()
  if (host) {
    return { available: host.available, latestVersion: host.to?.app ?? status.latest_version ?? '' }
  }
  const legacy = legacyInjectedVersion()
  const legacyNewer = !!legacy && !!status.current_version && cmpVersion(legacy, status.current_version) > 0
  return {
    available: !!status.update_available || legacyNewer,
    latestVersion: status.latest_version || legacy,
  }
}

export function SystemUpdateFooter() {
  const t = useT()
  const [status, setStatus] = useState<SystemUpdateStatus | null>(null)
  const [liveRequest, setLiveRequest] = useState<InstallRequestStatus | null>(null)
  const [confirmUpdate, confirmUpdateDialog] = useConfirmDialog()

  const updating = liveRequest?.state === 'pending' || liveRequest?.state === 'claimed'

  const poll = useCallback(() => {
    getSystemUpdate().then(setStatus)
    getInstallRequests().then((res) => {
      setLiveRequest(res.requests.find((r) => r.verb === 'update_system') ?? null)
    })
  }, [])

  useEffect(() => {
    poll()
    const id = setInterval(poll, updating ? SYSTEM_UPDATE_ACTIVE_POLL_MS : SYSTEM_UPDATE_POLL_MS)
    return () => clearInterval(id)
  }, [poll, updating])

  async function fireUpdate() {
    try {
      const res = await postInstallRequest('update_system')
      if (res.request) setLiveRequest(res.request)
      sileo.success({ title: t('sysupdate.toast.started') })
    } catch {
      sileo.error({ title: t('sysupdate.err.start') })
    }
  }

  async function handleUpdateClick() {
    const ok = await confirmUpdate({
      title: t('sysupdate.confirm.title'),
      description: t('sysupdate.confirm.body'),
      confirmLabel: t('sysupdate.confirm.ok'),
    })
    if (!ok) return
    await fireUpdate()
  }

  async function handleUninstallClick() {
    const ok = await confirmUpdate({
      title: t('sysuninstall.confirm.title'),
      description: t('sysuninstall.confirm.body'),
      confirmLabel: t('sysuninstall.confirm.ok'),
    })
    if (!ok) return
    try {
      await requestSystemUninstall()
      sileo.success({ title: t('sysuninstall.toast.started') })
    } catch {
      sileo.error({ title: t('sysuninstall.err.start') })
    }
  }

  if (!status?.current_version) return null

  const signal = resolveUpdateSignal(status)
  const available = !updating && signal.available
  const availableLabel = signal.latestVersion
    ? `${t('sysupdate.available')} · v${signal.latestVersion}`
    : t('sysupdate.available')
  const failed = !updating && liveRequest?.state === 'failed'
  const expired = !updating && liveRequest?.state === 'expired'
  const showAction = available || updating || failed || expired

  return (
    <div
      style={{
        display: 'flex', flexDirection: 'column', gap: 'var(--space-1)',
        padding: `var(--space-2) var(--space-4) var(--space-3)`,
        fontSize: 'var(--text-xs)', color: 'var(--color-text-dim)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', minWidth: 0 }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {t('sysupdate.current').replace('{v}', status.current_version)}
        </span>
        <span style={{ flex: 1 }} />
        {available && (
          <span
            title={availableLabel}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--color-accent)', whiteSpace: 'nowrap', maxWidth: '55%' }}
          >
            <span aria-hidden="true" style={{
              width: 6, height: 6, borderRadius: '50%', background: 'var(--color-accent)',
              animation: 'pulse-dot 1.6s ease-in-out infinite', flex: '0 0 auto',
            }} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {signal.latestVersion ? `v${signal.latestVersion}` : t('sysupdate.available')}
            </span>
          </span>
        )}
      </div>

      {updating && (
        <p aria-live="polite" style={{ margin: 0 }}>
          {t(stageLabelKey(liveRequest?.stage))}
          {formatProgress(liveRequest?.progress) && ` (${formatProgress(liveRequest?.progress)})`}
          {' — '}
          {t('sysupdate.relaunch_notice')}
        </p>
      )}

      {(failed || expired) && (
        <p role="alert" style={{ margin: 0, color: 'var(--color-danger)' }}>
          {failed
            ? (liveRequest?.last_failure?.label || t('sysupdate.err.generic'))
            : t('sysupdate.expired')}
        </p>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
        {showAction && (
          <button
            type="button"
            className="cv-btn cv-btn--ghost cv-btn--sm"
            style={{
              height: 'auto', padding: `3px var(--space-2)`, fontSize: 'var(--text-xs)',
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              flex: 1, minWidth: 0,
              ...(available ? {
                color: 'var(--color-accent)',
                borderColor: 'color-mix(in srgb, var(--color-accent) 45%, transparent)',
              } : {}),
            }}
            onClick={failed || expired ? fireUpdate : handleUpdateClick}
            disabled={updating}
            title={available ? availableLabel : undefined}
            aria-label={available ? `${availableLabel} — ${t('sysupdate.action')}` : t('sysupdate.action')}
          >
            <RefreshCw size={13} className={updating ? 'spin' : undefined} aria-hidden="true" style={{ flex: '0 0 auto' }} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {updating ? t('sysupdate.updating') : (failed || expired) ? t('sysupdate.retry') : t('sysupdate.action')}
            </span>
          </button>
        )}
        <button
          type="button"
          className="cv-btn cv-btn--ghost cv-btn--sm cv-btn--danger"
          style={{
            height: 'auto', padding: `3px 7px`,
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto',
          }}
          onClick={handleUninstallClick}
          disabled={updating}
          title={t('sysuninstall.action')}
          aria-label={t('sysuninstall.action')}
        >
          <Trash2 size={13} aria-hidden="true" />
        </button>
      </div>
      {confirmUpdateDialog}
    </div>
  )
}
