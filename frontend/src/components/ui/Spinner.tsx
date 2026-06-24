import { Loader2 } from 'lucide-react'

interface SpinnerProps {
  size?: number
  label?: string
}

export function Spinner({ size = 16, label = 'Cargando…' }: SpinnerProps) {
  return (
    <span
      role="status"
      aria-label={label}
      style={{ display: 'inline-flex', alignItems: 'center', color: 'var(--ink4)' }}
    >
      <Loader2
        size={size}
        aria-hidden="true"
        style={{ animation: 'spin 1s linear infinite' }}
      />
    </span>
  )
}
