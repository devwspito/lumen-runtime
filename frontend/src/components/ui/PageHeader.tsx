import type { ReactNode } from 'react'
import { AnimatedPageHeaderText } from './motion'

interface PageHeaderProps {
  title: string
  subtitle?: string
  actions?: ReactNode
}

export function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <header className="view-header ds-page-header">
      <div className="ds-page-header__left">
        <AnimatedPageHeaderText title={title} subtitle={subtitle} />
      </div>
      {actions && <div className="ds-page-header__actions">{actions}</div>}
    </header>
  )
}
