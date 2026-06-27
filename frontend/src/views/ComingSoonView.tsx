import { motion, useReducedMotion } from '../components/ui/motion'
import styles from './ComingSoonView.module.css'

interface ComingSoonViewProps {
  name: string
}

// ── Icon: a clock/soon glyph rendered in pure SVG — no external dep ──────────

function ClockIcon() {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M12 7v5.25l3.5 2"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function BackArrowIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M9 11L5 7l4-4"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

// ── Animated icon glow — mirrors AnimatedEmptyState pattern from motion.tsx ───

function AnimatedIconWrap({ children }: { children: React.ReactNode }) {
  const reduced = useReducedMotion()

  return (
    <motion.span
      className={styles.iconWrap}
      aria-hidden="true"
      animate={
        reduced
          ? undefined
          : {
              filter: [
                'drop-shadow(0 0 0px rgba(0, 145, 255, 0))',
                'drop-shadow(0 0 8px rgba(0, 145, 255, 0.3))',
                'drop-shadow(0 0 0px rgba(0, 145, 255, 0))',
              ],
            }
      }
      transition={
        reduced
          ? undefined
          : {
              duration: 3.6,
              repeat: Infinity,
              ease: 'easeInOut',
              delay: 0.8,
            }
      }
    >
      {children}
    </motion.span>
  )
}

// ── Main view ─────────────────────────────────────────────────────────────────

export default function ComingSoonView({ name }: ComingSoonViewProps) {
  const reduced = useReducedMotion()

  const handleBack = () => {
    if (typeof window !== 'undefined' && window.history.length > 1) {
      window.history.back()
    }
  }

  return (
    <main
      className={styles.root}
      role="main"
      aria-label={`${name} — próximamente`}
    >
      <motion.div
        className={styles.card}
        initial={reduced ? false : { opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{
          type: 'spring',
          stiffness: 420,
          damping: 34,
          mass: 0.7,
        }}
      >
        {/* Status pill */}
        <div role="status" aria-live="polite">
          <span className={styles.pill}>
            <span className={styles.pillDot} />
            En desarrollo
          </span>
        </div>

        {/* Icon */}
        <AnimatedIconWrap>
          <ClockIcon />
        </AnimatedIconWrap>

        {/* Text */}
        <motion.div
          className={styles.textBlock}
          initial={reduced ? false : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{
            type: 'spring',
            stiffness: 420,
            damping: 34,
            mass: 0.7,
            delay: 0.07,
          }}
        >
          <h1 className={styles.headline}>
            <span className={styles.featureName}>{name}</span>{' '}
            está en camino
          </h1>
          <p className={styles.body}>
            Esta sección estará disponible en una próxima versión. Continuamos
            ampliando el producto.
          </p>
        </motion.div>

        {/* Divider + back action */}
        <motion.div
          style={{ width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 'var(--space-4)' }}
          initial={reduced ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{
            type: 'tween',
            ease: [0.4, 0, 0.2, 1],
            duration: 0.22,
            delay: 0.18,
          }}
        >
          <hr className={styles.divider} aria-hidden="true" />
          <button
            type="button"
            className={styles.backRow}
            onClick={handleBack}
            aria-label="Volver a la sección anterior"
          >
            <span className={styles.backArrow}>
              <BackArrowIcon />
            </span>
            Volver
          </button>
        </motion.div>
      </motion.div>
    </main>
  )
}
