/**
 * PendingApprovalsInChat — polls for HITL approvals and renders those that
 * belong to the currently active conversation inside the chat message list.
 *
 * Decision — conversation_id === null:
 *   Approvals without a conversation_id were written before the migration that
 *   added that column. Showing them in every chat would be noisy and confusing
 *   (the user wouldn't know which agent triggered them). We therefore show them
 *   only in SeguridadView (the full list), NOT here. Rationale: the Security
 *   view is always one click away and serves as the authoritative HITL queue;
 *   the in-chat widget is a convenience shortcut for the active turn only.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { listPendingApprovals } from '../api/client'
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
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(async () => {
    const all = await listPendingApprovals()
    if (!Array.isArray(all)) return

    // Keep only approvals that belong to the active conversation.
    // Approvals with conversation_id === null are intentionally excluded here
    // (see file-level decision comment above).
    const filtered = currentThreadId
      ? all.filter(a => a.conversation_id === currentThreadId)
      : []

    setApprovals(filtered)
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
          onResolved={() => { void load() }}
        />
      ))}
    </div>
  )
}
