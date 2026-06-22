import { useEffect, useState } from 'react'
import { listConversations } from '../api/client'
import type { ConversationSummary } from '../api/types'

function relativeTime(iso?: string): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return 'Ahora'
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h`
  return `${Math.floor(hours / 24)}d`
}

interface UseConversationsReturn {
  conversations: ConversationSummary[]
  relTime(iso?: string): string
  reload(): void
}

export function useConversations(): UseConversationsReturn {
  const [conversations, setConversations] = useState<ConversationSummary[]>([])

  function load() {
    listConversations().then(setConversations).catch(() => setConversations([]))
  }

  useEffect(() => {
    load()
  }, [])

  return { conversations, relTime: relativeTime, reload: load }
}
