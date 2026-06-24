import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Loader2 } from 'lucide-react'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'danger-solid'
export type ButtonSize = 'sm' | 'md'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  children: ReactNode
}

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  disabled,
  children,
  className = '',
  ...rest
}: ButtonProps) {
  const variantClass = {
    primary: 'cv-btn--primary',
    secondary: 'cv-btn--secondary',
    ghost: 'cv-btn--ghost',
    danger: 'cv-btn--ghost cv-btn--danger',
    'danger-solid': 'cv-btn--danger-solid cv-btn--ghost',
  }[variant]

  const sizeClass = size === 'sm' ? 'cv-btn--sm' : ''

  return (
    <button
      className={`cv-btn ${variantClass} ${sizeClass} ${className}`.trim()}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && (
        <Loader2
          size={14}
          aria-hidden="true"
          style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }}
        />
      )}
      {children}
    </button>
  )
}
