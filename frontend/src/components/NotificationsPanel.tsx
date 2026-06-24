/**
 * NotificationsPanel — bell icon + dropdown panel for the sidebar.
 *
 * - Polls unread-count every ~10s (matching the approvals badge pattern).
 * - On open: loads full notification list; marks individual reads on click;
 *   offers "Marcar todo como leído" bulk action.
 * - Clicking a notification with a conversation_id navigates to /chat and
 *   loads that conversation.
 */

import { createPortal } from 'react-dom'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { sileo } from 'sileo'
import { Circle } from 'lucide-react'
import {
  listNotifications,
  getUnreadCount,
  markNotificationRead,
  markAllNotificationsRead,
} from '../api/client'
import type { Notification, NotificationStatus } from '../api/types'
import Badge, { type BadgeVariant } from './Badge'

const STATUS_VARIANT: Record<NotificationStatus, BadgeVariant> = {
  ok: 'ok',
  error: 'danger',
  info: 'info',
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return 'Ahora'
  if (mins < 60) return `Hace ${mins} min`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `Hace ${hrs} h`
  return `Hace ${Math.floor(hrs / 24)} d`
}

function truncate(s: string, n: number) {
  return s.length > n ? s.slice(0, n) + '…' : s
}

function BellIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M8 2a5 5 0 0 1 5 5v2.5l1 1.5H2l1-1.5V7a5 5 0 0 1 5-5ZM6.5 13.5a1.5 1.5 0 0 0 3 0"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </svg>
  )
}

interface NotificationsPanelProps {
  /** Called when the user navigates to a conversation from the panel */
  loadConversation(id: string): Promise<void>
}

export default function NotificationsPanel({ loadConversation }: NotificationsPanelProps) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [unreadCount, setUnreadCount] = useState(0)
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [panelLoading, setPanelLoading] = useState(false)
  const [markingAll, setMarkingAll] = useState(false)
  const btnRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const aliveRef = useRef(true)

  // Poll unread count every 10 s
  useEffect(() => {
    aliveRef.current = true
    const poll = () => {
      getUnreadCount()
        .then(r => { if (aliveRef.current) setUnreadCount(r.count ?? 0) })
        .catch(() => { /* keep last known */ })
    }
    poll()
    const id = setInterval(poll, 10_000)
    return () => {
      aliveRef.current = false
      clearInterval(id)
    }
  }, [])

  const loadPanel = useCallback(() => {
    setPanelLoading(true)
    listNotifications()
      .then(data => {
        if (aliveRef.current) {
          setNotifications(Array.isArray(data) ? data : [])
        }
      })
      .catch(() => { /* silent */ })
      .finally(() => { if (aliveRef.current) setPanelLoading(false) })
  }, [])

  function handleOpen() {
    setOpen(v => !v)
    if (!open) loadPanel()
  }

  // Close on Escape or outside click
  useEffect(() => {
    if (!open) return
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setOpen(false)
        btnRef.current?.focus()
      }
    }
    function handleClick(e: MouseEvent) {
      if (
        panelRef.current && !panelRef.current.contains(e.target as Node) &&
        btnRef.current && !btnRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener('keydown', handleKey, true)
    document.addEventListener('mousedown', handleClick)
    return () => {
      document.removeEventListener('keydown', handleKey, true)
      document.removeEventListener('mousedown', handleClick)
    }
  }, [open])

  async function handleNotificationClick(n: Notification) {
    if (!n.read) {
      markNotificationRead(n.id).catch(() => { /* silent */ })
      setNotifications(prev => prev.map(x => x.id === n.id ? { ...x, read: true } : x))
      setUnreadCount(c => Math.max(0, c - 1))
    }
    if (n.conversation_id) {
      setOpen(false)
      navigate('/chat')
      await loadConversation(n.conversation_id)
    }
  }

  async function handleMarkAll() {
    setMarkingAll(true)
    try {
      await markAllNotificationsRead()
      setNotifications(prev => prev.map(n => ({ ...n, read: true })))
      setUnreadCount(0)
      sileo.success({ title: 'Todas las notificaciones marcadas como leídas' })
    } catch {
      sileo.error({ title: 'No se pudieron marcar las notificaciones' })
    } finally {
      setMarkingAll(false)
    }
  }

  // Position the floating panel relative to the button
  const [panelPos, setPanelPos] = useState({ top: 0, left: 0 })
  useEffect(() => {
    if (!open || !btnRef.current) return
    const rect = btnRef.current.getBoundingClientRect()
    // Place below the button, aligned to its left edge
    setPanelPos({ top: rect.bottom + 8, left: rect.left })
  }, [open])

  const hasUnread = unreadCount > 0

  return (
    <>
      {/* Bell trigger */}
      <button
        ref={btnRef}
        type="button"
        className="notif-bell-btn"
        aria-label={hasUnread ? `${unreadCount} notificaciones sin leer` : 'Notificaciones'}
        aria-expanded={open}
        aria-haspopup="true"
        onClick={handleOpen}
      >
        <BellIcon />
        {hasUnread && (
          <span
            className="notif-bell-badge"
            aria-hidden="true"
          >
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {/* Floating panel via portal */}
      {open && createPortal(
        <div
          ref={panelRef}
          className="notif-panel"
          role="dialog"
          aria-label="Notificaciones"
          aria-modal="false"
          style={{ top: panelPos.top, left: panelPos.left }}
        >
          <div className="notif-panel__header">
            <span className="notif-panel__title">Notificaciones</span>
            {notifications.some(n => !n.read) && (
              <button
                type="button"
                className="cv-btn cv-btn--ghost cv-btn--sm"
                onClick={handleMarkAll}
                disabled={markingAll}
              >
                {markingAll ? 'Marcando…' : 'Marcar todo como leído'}
              </button>
            )}
          </div>

          <div className="notif-panel__body" role="list">
            {panelLoading && (
              <div className="notif-panel__loading" aria-busy="true">
                <div className="cv-skeleton" style={{ height: 52 }} />
                <div className="cv-skeleton" style={{ height: 52 }} />
              </div>
            )}
            {!panelLoading && notifications.length === 0 && (
              <div className="notif-panel__empty" role="listitem">Sin notificaciones.</div>
            )}
            {!panelLoading && notifications.map(n => (
              <button
                key={n.id}
                type="button"
                role="listitem"
                className={`notif-item${n.read ? '' : ' notif-item--unread'}${n.conversation_id ? ' notif-item--link' : ''}`}
                onClick={() => handleNotificationClick(n)}
              >
                <div className="notif-item__left">
                  <Badge variant={STATUS_VARIANT[n.status]}><Circle size={8} aria-hidden="true" style={{ display: 'block', fill: 'currentColor' }} /></Badge>
                </div>
                <div className="notif-item__body">
                  <div className="notif-item__title">{n.title}</div>
                  {n.body && (
                    <div className="notif-item__body-text" title={n.body}>
                      {truncate(n.body, 80)}
                    </div>
                  )}
                  <div className="notif-item__time">{relativeTime(n.created_at)}</div>
                </div>
                {n.conversation_id && (
                  <div className="notif-item__arrow" aria-hidden="true">›</div>
                )}
              </button>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </>
  )
}
