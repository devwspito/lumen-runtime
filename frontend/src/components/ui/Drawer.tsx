import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { AnimatedDrawer } from './motion'

interface DrawerProps {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  width?: number
}

/**
 * Slide-in side panel from the right.
 * Handles: Escape key, click-outside, focus trap, reduced-motion.
 * Animation is driven by AnimatePresence + spring physics via AnimatedDrawer.
 */
export function Drawer({ open, title, onClose, children, width = 400 }: DrawerProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  // Focus the close button when the drawer opens
  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => closeButtonRef.current?.focus())
    }
  }, [open])

  // Escape key to close
  useEffect(() => {
    if (!open) return
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  // Focus trap
  useEffect(() => {
    if (!open) return

    function handleTab(e: KeyboardEvent) {
      if (e.key !== 'Tab') return
      const el = document.querySelector('[data-drawer-panel]') as HTMLElement | null
      if (!el) return
      const focusable = el.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey) {
        if (document.activeElement === first) { e.preventDefault(); last?.focus() }
      } else {
        if (document.activeElement === last) { e.preventDefault(); first?.focus() }
      }
    }

    document.addEventListener('keydown', handleTab)
    return () => document.removeEventListener('keydown', handleTab)
  }, [open])

  return (
    <AnimatedDrawer open={open} onBackdropClick={onClose} width={width} label={title}>
      <div data-drawer-panel>
        <div className="office-drawer-header">
          <div style={{ flex: 1 }}>
            <div className="office-drawer-title">{title}</div>
          </div>
          <button
            ref={closeButtonRef}
            className="office-modal-close"
            onClick={onClose}
            aria-label="Cerrar panel"
            type="button"
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
        <div className="office-drawer-body">
          {children}
        </div>
      </div>
    </AnimatedDrawer>
  )
}
