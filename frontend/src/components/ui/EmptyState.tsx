import type { ReactNode } from 'react'
import { AnimatedEmptyState } from './motion'

interface EmptyStateProps {
  icon: ReactNode
  title: string
  description?: string
  action?: ReactNode
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <AnimatedEmptyState
      icon={icon}
      title={title}
      description={description}
      action={action}
    />
  )
}
