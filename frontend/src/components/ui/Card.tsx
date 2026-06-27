import { forwardRef, type HTMLAttributes, type ReactNode } from 'react'

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode
  /** Adds a subtle lift + border transition on pointer hover. */
  hoverable?: boolean
}

export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { children, hoverable = false, className = '', ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      className={`ds-card${hoverable ? ' ds-card--hoverable' : ''} ${className}`.trim()}
      {...rest}
    >
      {children}
    </div>
  )
})

interface CardHeaderProps {
  title: string
  subtitle?: string
  action?: ReactNode
}

export function CardHeader({ title, subtitle, action }: CardHeaderProps) {
  return (
    <div className="ds-card__header">
      <div className="ds-card__header-text">
        <h3 className="ds-card__title">{title}</h3>
        {subtitle ? <p className="ds-card__subtitle">{subtitle}</p> : null}
      </div>
      {action ? <div className="ds-card__action">{action}</div> : null}
    </div>
  )
}
