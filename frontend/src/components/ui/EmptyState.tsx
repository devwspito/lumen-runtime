import type { ReactNode } from 'react'

interface EmptyStateProps {
  icon: ReactNode
  title: string
  description?: string
  action?: ReactNode
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="ds-empty-state">
      <span className="ds-empty-state__icon" aria-hidden="true">{icon}</span>
      <p className="ds-empty-state__title">{title}</p>
      {description && <p className="ds-empty-state__desc">{description}</p>}
      {action && <div className="ds-empty-state__action">{action}</div>}
    </div>
  )
}
