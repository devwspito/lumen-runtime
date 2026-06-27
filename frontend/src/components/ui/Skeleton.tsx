import styles from './Skeleton.module.css'

export interface SkeletonProps {
  /** Explicit width. Defaults to 100%. */
  width?: string
  /** Explicit height. Defaults to 1em. */
  height?: string
  className?: string
}

/** Inline skeleton block. Mirror the dimensions of the content it replaces. */
export function Skeleton({ width, height = '1em', className = '' }: SkeletonProps) {
  return (
    <span
      className={`${styles.skeleton} ${className}`.trim()}
      style={{ width, height }}
      aria-hidden
    />
  )
}

/** Skeleton that mirrors a list row (icon + two text lines + trailing value). */
export function SkeletonRow() {
  return (
    <div className={styles.row} aria-hidden>
      <Skeleton width="140px" height="13px" />
      <Skeleton width="80px" height="13px" />
      <Skeleton width="60px" height="13px" />
    </div>
  )
}

/** Skeleton that mirrors a KPI/metric card layout. */
export function SkeletonCard() {
  return (
    <div className={styles.card} aria-hidden>
      <Skeleton width="80px" height="13px" />
      <Skeleton width="120px" height="32px" />
      <Skeleton width="60px" height="11px" />
    </div>
  )
}
