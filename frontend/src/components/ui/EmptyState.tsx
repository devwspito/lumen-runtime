import type { ReactNode } from 'react'
import { AnimatedEmptyState } from './motion'

export interface EmptyStateProps {
  /** Icon node — displayed with a subtle glow pulse animation. */
  icon: ReactNode
  /** Primary message. Use business language; avoid AI jargon. */
  title: string
  /** Secondary description. Keep concise and actionable. */
  description?: string
  /** CTA button or link. Rendered below description. */
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
