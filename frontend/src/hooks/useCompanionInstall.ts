/**
 * useCompanionInstall — the ONE install/repair flow for the Ads companion
 * (029 FR-001/FR-005/FR-008), shared by the Herramientas card
 * (CompanionInstallAction inside McpView) and the sidebar's `not_installed`
 * state (AdsNavItem). Owns the install-request lifecycle only; the actual
 * "is it really ready" signal comes from useAdsAvailability (the same-origin
 * bridge health check) — an `applied` request is NOT enough on its own
 * (contracts/install-request.md §5: "Prohibido mostrar ready sin que el
 * puente haya respondido de verdad").
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { postInstallRequest, getInstallRequests } from '../api/client'
import type { HostVerb, InstallRequestStatus } from '../api/types'
import type { AdsAvailability } from './useAdsAvailability'

const ACTIVE_POLL_MS = 5_000
const COMPANION_SLUG = 'safent-ads'

export type CompanionInstallPhase =
  /** Ready, loading, or a reason this flow has no action for (unauthorized/no_accounts). */
  | { kind: 'hidden' }
  | { kind: 'install' }
  | { kind: 'repair' }
  | { kind: 'installing'; stage?: string; progress?: InstallRequestStatus['progress'] }
  | { kind: 'expired' }
  | { kind: 'failed'; label?: string; retryable: boolean }

export interface CompanionInstall {
  phase: CompanionInstallPhase
  install: () => void
  repair: () => void
  retry: () => void
}

function isLive(status: InstallRequestStatus | null): boolean {
  return status?.state === 'pending' || status?.state === 'claimed'
}

/** Pure — the phase is a function of (what the last known install-request says, what the
 *  companion's own health check says). Testable without touching the hook's plumbing. */
export function deriveCompanionInstallPhase(
  availability: Pick<AdsAvailability, 'status' | 'reason'>,
  status: InstallRequestStatus | null,
): CompanionInstallPhase {
  switch (status?.state) {
    case 'pending':
    case 'claimed':
      return { kind: 'installing', stage: status.stage, progress: status.progress }
    case 'applied':
      // Contract: applied alone is not ready — keep showing progress until the
      // bridge actually answers, so a slow warm-up never LOOKS like a failure.
      if (availability.status !== 'ready') {
        return { kind: 'installing', stage: status.stage, progress: status.progress }
      }
      break
    case 'expired':
      return { kind: 'expired' }
    case 'failed':
      return {
        kind: 'failed',
        label: status.last_failure?.label,
        retryable: status.last_failure?.retryable ?? true,
      }
  }
  if (availability.status === 'ready' || availability.status === 'loading') return { kind: 'hidden' }
  if (availability.reason === 'not_installed') return { kind: 'install' }
  if (availability.reason === 'unreachable') return { kind: 'repair' }
  return { kind: 'hidden' } // unauthorized / no_accounts — onboarding's job, not install's
}

export function useCompanionInstall(availability: AdsAvailability): CompanionInstall {
  const [status, setStatus] = useState<InstallRequestStatus | null>(null)
  const aliveRef = useRef(true)
  const submittingRef = useRef(false)
  const prevAppliedRef = useRef(false)

  const poll = useCallback(() => {
    getInstallRequests().then((res) => {
      if (!aliveRef.current) return
      const live = res.requests.find(
        (r) => r.verb === 'install_companion' || r.verb === 'repair_companion',
      )
      setStatus(live ?? null)
    })
  }, [])

  useEffect(() => {
    aliveRef.current = true
    poll() // pick up a request already in flight from the OTHER surface (sidebar vs. card)
    return () => { aliveRef.current = false }
  }, [poll])

  const active = isLive(status)
  useEffect(() => {
    if (!active) return
    const id = setInterval(poll, ACTIVE_POLL_MS)
    return () => clearInterval(id)
  }, [active, poll])

  // The moment a request reaches "applied", nudge the bridge check immediately
  // instead of waiting up to its own poll interval — shortens the honest
  // "still warming up" window before we can call it ready.
  useEffect(() => {
    const applied = status?.state === 'applied'
    if (applied && !prevAppliedRef.current) availability.refresh()
    prevAppliedRef.current = applied
  }, [status, availability])

  const fire = useCallback((verb: HostVerb) => {
    if (active || submittingRef.current) return // FR-008: never a second install while one is live
    submittingRef.current = true
    postInstallRequest(verb, { slug: COMPANION_SLUG })
      .then((res) => {
        if (!aliveRef.current) return
        if (res.request) setStatus(res.request)
      })
      .catch(() => {
        if (!aliveRef.current) return
        setStatus({ verb, state: 'failed', expires_at: new Date().toISOString() })
      })
      .finally(() => { submittingRef.current = false })
  }, [active])

  const install = useCallback(() => fire('install_companion'), [fire])
  const repair = useCallback(() => fire('repair_companion'), [fire])
  const retry = useCallback(() => fire(status?.verb ?? 'install_companion'), [fire, status])

  const phase = deriveCompanionInstallPhase(availability, status)
  return { phase, install, repair, retry }
}
