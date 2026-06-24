/**
 * Badge — unified status/label pill used across all capability views.
 * Replaces the scattered .provider-badge, .skill-state-chip, .mcp-health-chip,
 * and inline badge styles.
 */

export type BadgeVariant = 'ok' | 'warn' | 'danger' | 'info' | 'neutral' | 'accent'

interface BadgeProps {
  variant?: BadgeVariant
  children: React.ReactNode
  /** Forwarded to title for long text that gets truncated upstream */
  title?: string
}

const VARIANT_STYLES: Record<BadgeVariant, React.CSSProperties> = {
  ok: {
    background: 'color-mix(in srgb, var(--ok) 14%, transparent)',
    color: 'var(--ok)',
  },
  warn: {
    background: 'color-mix(in srgb, var(--warn) 14%, transparent)',
    color: 'var(--warn)',
  },
  danger: {
    background: 'color-mix(in srgb, var(--danger) 14%, transparent)',
    color: 'var(--danger)',
  },
  info: {
    background: 'color-mix(in srgb, var(--info) 14%, transparent)',
    color: 'var(--info)',
  },
  neutral: {
    background: 'var(--surface2)',
    color: 'var(--ink3)',
  },
  accent: {
    background: 'color-mix(in srgb, var(--accent) 14%, transparent)',
    color: 'var(--accent)',
  },
}

const BASE_STYLE: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  fontSize: 'var(--text-micro)',
  fontWeight: 600,
  padding: '2px 7px',
  borderRadius: '100px',
  whiteSpace: 'nowrap',
  lineHeight: 1.4,
  letterSpacing: '0.01em',
  flexShrink: 0,
}

export default function Badge({ variant = 'neutral', children, title }: BadgeProps) {
  return (
    <span
      style={{ ...BASE_STYLE, ...VARIANT_STYLES[variant] }}
      title={title}
    >
      {children}
    </span>
  )
}
