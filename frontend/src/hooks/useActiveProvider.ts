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

import { useCallback, useEffect, useState } from 'react'
import { listProviders } from '../api/client'

type Status = 'loading' | 'ready' | 'error'

export interface ActiveProviderState {
  status: Status
  hasActive: boolean
  reload(): void
}

export function useActiveProvider(): ActiveProviderState {
  const [status, setStatus] = useState<Status>('loading')
  const [hasActive, setHasActive] = useState(false)

  const reload = useCallback(() => {
    setStatus('loading')
    listProviders()
      .then(providers => {
        const active = Array.isArray(providers) && providers.some(p => p.is_active)
        setHasActive(active)
        setStatus('ready')
      })
      .catch(() => {
        // If the call fails we default to "no active provider" so the gate
        // doesn't permanently block navigation.  The user can retry from
        // the onboarding wizard.
        setHasActive(false)
        setStatus('error')
      })
  }, [])

  useEffect(() => { reload() }, [reload])

  return { status, hasActive, reload }
}
