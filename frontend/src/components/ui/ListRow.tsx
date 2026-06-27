import { forwardRef, type HTMLAttributes, type ReactNode } from 'react'

export interface ListRowProps extends HTMLAttributes<HTMLDivElement> {
  /** Leading icon or avatar node. */
  icon?: ReactNode
  /** Primary label — can be a string or a composed node. */
  label: ReactNode
  /** Secondary metadata line below the label. */
  meta?: ReactNode
  /** Trailing actions (buttons, badges). */
  actions?: ReactNode
  /** Applies hover styling and cursor pointer for interactive rows. */
  clickable?: boolean
}

export const ListRow = forwardRef<HTMLDivElement, ListRowProps>(function ListRow(
  { icon, label, meta, actions, clickable = false, className = '', ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      className={`ds-list-row${clickable ? ' ds-list-row--clickable' : ''} ${className}`.trim()}
      {...rest}
    >
      {icon ? <span className="ds-list-row__icon" aria-hidden>{icon}</span> : null}
      <div className="ds-list-row__body">
        <div className="ds-list-row__title">{label}</div>
        {meta ? <div className="ds-list-row__meta">{meta}</div> : null}
      </div>
      {actions ? <div className="ds-list-row__actions">{actions}</div> : null}
    </div>
  )
})
