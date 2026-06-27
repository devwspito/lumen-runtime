import type { ReactNode } from 'react'
import { AnimatedPageHeaderText } from './motion'

export interface PageHeaderProps {
  title: string
  subtitle?: string
  /** Trailing controls rendered flush right. */
  actions?: ReactNode
}

export function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <header className="view-header ds-page-header">
      <div className="ds-page-header__left">
        <AnimatedPageHeaderText title={title} subtitle={subtitle} />
      </div>
      {actions ? <div className="ds-page-header__actions">{actions}</div> : null}
    </header>
  )
}
