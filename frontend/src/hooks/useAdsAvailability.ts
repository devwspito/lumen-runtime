/**
 * useAdsAvailability — Ads is a first-level sidebar entry, ALWAYS visible
 * (spec 026 FR-001/Assumption 7: "sin cuentas conectadas, Ads sigue
 * visible"), never hidden behind a connection check like the legacy
 * useAdsPanelOrigin it replaces. This hook only drives the disabled/enabled
 * *presentation* of that entry and pre-warms the same-origin session bridge
 * (contracts/sso.md) so the FIRST click into Ads never shows a login step
 * (SC-002: zero-second logins across 20 opens).
 *
 * Polls POST /api/v1/ads/bridge/session — same call AdsView's first paint
 * would otherwise have to make, so polling it from the sidebar means the
 * bridge cookie is already warm by the time the owner clicks through.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { mintAdsBridgeSession } from '../api/client'
import type { AdsAvailabilityReason } from '../api/types'

const ADS_AVAILABILITY_POLL_MS = 15_000

export interface AdsAvailability {
  /** "loading" only before the first response ever arrives. */
  status: 'loading' | 'ready' | 'unavailable'
  reason: AdsAvailabilityReason | null
  /** Re-checks immediately, outside the poll interval (e.g. a "Retry" CTA). */
  refresh: () => void
}

export function useAdsAvailability(pollMs = ADS_AVAILABILITY_POLL_MS): AdsAvailability {
  const [state, setState] = useState<{ status: 'loading' | 'ready' | 'unavailable'; reason: AdsAvailabilityReason | null }>(
    { status: 'loading', reason: null },
  )
  const aliveRef = useRef(true)

  const poll = useCallback(() => {
    // mintAdsBridgeSession never rejects (fail-soft in the client) — a
    // transient failure already resolves to "unavailable".
    mintAdsBridgeSession().then((res) => {
      if (!aliveRef.current) return
      setState({ status: res.status, reason: res.reason })
    })
  }, [])

  useEffect(() => {
    aliveRef.current = true
    poll()
    const id = setInterval(poll, pollMs)
    return () => { aliveRef.current = false; clearInterval(id) }
  }, [poll, pollMs])

  return { ...state, refresh: poll }
}
