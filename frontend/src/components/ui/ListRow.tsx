import type { HTMLAttributes, ReactNode } from 'react'

// Renamed to `label` to avoid shadowing HTMLAttributes.title (string | undefined)
interface ListRowProps extends HTMLAttributes<HTMLDivElement> {
  icon?: ReactNode
  label: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  clickable?: boolean
}

export function ListRow({
  icon,
  label,
  meta,
  actions,
  clickable = false,
  className = '',
  ...rest
}: ListRowProps) {
  return (
    <div
      className={`ds-list-row${clickable ? ' ds-list-row--clickable' : ''} ${className}`.trim()}
      {...rest}
    >
      {icon && <span className="ds-list-row__icon" aria-hidden="true">{icon}</span>}
      <div className="ds-list-row__body">
        <div className="ds-list-row__title">{label}</div>
        {meta && <div className="ds-list-row__meta">{meta}</div>}
      </div>
      {actions && <div className="ds-list-row__actions">{actions}</div>}
    </div>
  )
}
