import type { ReactNode } from 'react'
import styles from './Badge.module.css'

export type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'accent'

export interface BadgeProps {
  variant?: BadgeVariant
  children: ReactNode
}

/**
 * Token-driven status/label pill.
 * Use `variant` to convey semantic meaning — never color alone.
 */
export function Badge({ variant = 'default', children }: BadgeProps) {
  return (
    <span className={`${styles.badge} ${styles[variant]}`}>
      {children}
    </span>
  )
}

export type StatusDotState = 'success' | 'warning' | 'danger' | 'default'

export interface StatusDotProps {
  state: StatusDotState
  /** Visible label next to the dot. */
  label?: string
}

/**
 * Inline status indicator — dot + optional label.
 * Always includes an accessible aria-label on the container.
 */
export function StatusDot({ state, label }: StatusDotProps) {
  return (
    <span className={styles.statusRow} aria-label={label ?? state}>
      <span className={`${styles.dot} ${styles[`dot_${state}`]}`} aria-hidden />
      {label != null ? <span className={styles.statusLabel}>{label}</span> : null}
    </span>
  )
}
