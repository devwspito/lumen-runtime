import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { Loader2 } from 'lucide-react'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'danger-solid'
export type ButtonSize = 'sm' | 'md'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Shows a loading spinner and prevents interaction while true. */
  loading?: boolean
  children: ReactNode
}

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary:       'cv-btn--primary',
  secondary:     'cv-btn--secondary',
  ghost:         'cv-btn--ghost',
  danger:        'cv-btn--ghost cv-btn--danger',
  'danger-solid':'cv-btn--danger-solid cv-btn--ghost',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    loading = false,
    disabled,
    children,
    className = '',
    ...rest
  },
  ref,
) {
  const sizeClass = size === 'sm' ? 'cv-btn--sm' : ''

  return (
    <button
      ref={ref}
      className={`cv-btn ${VARIANT_CLASS[variant]} ${sizeClass} ${className}`.trim()}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && (
        <Loader2
          size={14}
          aria-hidden
          style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }}
        />
      )}
      {children}
    </button>
  )
})
