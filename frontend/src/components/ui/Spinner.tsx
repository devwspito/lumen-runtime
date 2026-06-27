import { Loader2 } from 'lucide-react'

export interface SpinnerProps {
  /** Icon size in px. Defaults to 16. */
  size?: number
  /** Screen-reader label. Defaults to "Cargando…". */
  label?: string
}

export function Spinner({ size = 16, label = 'Cargando…' }: SpinnerProps) {
  return (
    <span
      role="status"
      aria-label={label}
      style={{ display: 'inline-flex', alignItems: 'center', color: 'var(--color-text-muted)' }}
    >
      <Loader2
        size={size}
        aria-hidden
        style={{ animation: 'spin 1s linear infinite' }}
      />
    </span>
  )
}
