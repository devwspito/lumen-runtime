import type { HTMLAttributes, ReactNode } from 'react'

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode
  hoverable?: boolean
}

export function Card({ children, hoverable = false, className = '', ...rest }: CardProps) {
  return (
    <div
      className={`ds-card${hoverable ? ' ds-card--hoverable' : ''} ${className}`.trim()}
      {...rest}
    >
      {children}
    </div>
  )
}
