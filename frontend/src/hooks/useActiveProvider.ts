/**
 * useActiveProvider — checks whether there is an active (configured) provider.
 *
 * Calls listProviders() once on mount and exposes:
 *   - status: 'loading' | 'ready' | 'error'
 *   - hasActive: true when at least one provider is marked is_active
 *   - reload(): re-fetches (call after the user connects a model)
 *
 * This is the single source of truth that the onboarding gate (App / Layout)
 * and the sidebar badge both read from.  We use listProviders() — not a
 * dedicated /active endpoint — because the backend may not have that endpoint
 * in all versions and the configured list is always present.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { listProviders } from '../api/client'

type Status = 'loading' | 'ready' | 'error'

export interface ActiveProviderState {
  status: Status
  hasActive: boolean
  reload(): void
}

const POLL_INTERVAL_MS = 5_000

export function useActiveProvider(): ActiveProviderState {
  const [status, setStatus] = useState<Status>('loading')
  const [hasActive, setHasActive] = useState(false)
  // Avoid setting state after unmount
  const alive = useRef(true)

  const fetch = useCallback(() => {
    listProviders()
      .then(providers => {
        if (!alive.current) return
        const active = Array.isArray(providers) && providers.some(p => p.is_active)
        setHasActive(active)
        setStatus('ready')
      })
      .catch(() => {
        if (!alive.current) return
        // On error keep last hasActive value so a transient failure doesn't
        // flash the "no model" nudge while the owner's provider is working.
        setStatus('error')
      })
  }, [])

  const reload = useCallback(() => {
    setStatus('loading')
    fetch()
  }, [fetch])

  useEffect(() => {
    alive.current = true
    fetch()
    const id = setInterval(fetch, POLL_INTERVAL_MS)
    return () => {
      alive.current = false
      clearInterval(id)
    }
  }, [fetch])

  return { status, hasActive, reload }
}
