/**
 * PendingApprovalsInChat — polls for HITL approvals and renders those that
 * belong to the currently active conversation (or orphan approvals with no
 * conversation_id that could block any agent cycle) inside the chat message list.
 *
 * Flash prevention: we only replace the rendered list when a poll SUCCEEDS.
 * A transient poll failure or in-flight state keeps the previous list visible
 * so cards never disappear for a frame between polls.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { listPendingApprovals, getPolicies } from '../api/client'
import type { PendingApproval } from '../api/types'
import ApprovalCard from './ApprovalCard'

const POLL_INTERVAL_MS = 3000

interface PendingApprovalsInChatProps {
  currentThreadId: string | null
  /** Incremented externally (e.g. on message send) to force an immediate refresh. */
  refreshTick: number
}

export default function PendingApprovalsInChat({
  currentThreadId,
  refreshTick,
}: PendingApprovalsInChatProps) {
  const [approvals, setApprovals] = useState<PendingApproval[]>([])
  const [mfaDisabled, setMfaDisabled] = useState(false)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(async () => {
    try {
      const [all, pol] = await Promise.all([listPendingApprovals(), getPolicies()])
      if (!Array.isArray(all)) return

      // Show approvals belonging to the active conversation, PLUS orphan ones
      // (conversation_id null/empty) that may come from scheduled/autonomous
      // cycles — they are never attached to a thread but still block the agent.
      const filtered = all.filter(a =>
        (currentThreadId && a.conversation_id === currentThreadId) ||
        !a.conversation_id
      )

      // Only update state on a successful poll so a transient empty response
      // does not flash-clear cards that are still pending.
      setApprovals(filtered)
      setMfaDisabled(pol.mfa_on_dangers === false)
    } catch {
      // Transient failure — keep last known approvals visible.
    }
  }, [currentThreadId])

  // Start/restart poll whenever the active thread changes.
  useEffect(() => {
    if (intervalRef.current !== null) clearInterval(intervalRef.current)
    void load()
    intervalRef.current = setInterval(() => { void load() }, POLL_INTERVAL_MS)
    return () => {
      if (intervalRef.current !== null) clearInterval(intervalRef.current)
    }
  }, [load])

  // Force immediate refresh when the parent bumps refreshTick (e.g. on send).
  useEffect(() => {
    if (refreshTick > 0) void load()
  }, [refreshTick, load])

  if (approvals.length === 0) return null

  return (
    <div
      className="cv-list"
      aria-label="Aprobaciones pendientes en esta conversación"
      aria-live="polite"
    >
      {approvals.map(a => (
        <ApprovalCard
          key={a.proposal_id}
          approval={a}
          mfaDisabled={mfaDisabled}
          onResolved={() => { void load() }}
        />
      ))}
    </div>
  )
}
