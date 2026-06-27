import { forwardRef, type InputHTMLAttributes, type SelectHTMLAttributes, type TextareaHTMLAttributes, type ReactNode } from 'react'
import styles from './Input.module.css'

interface FieldMeta {
  label: string
  /** Field id — drives htmlFor + aria-describedby pairing. */
  fieldId: string
  error?: string
  hint?: string
}

/* ── InputField ─────────────────────────────────────────────────────────────── */

export type InputFieldProps = FieldMeta & Omit<InputHTMLAttributes<HTMLInputElement>, 'id'>

export const InputField = forwardRef<HTMLInputElement, InputFieldProps>(function InputField(
  { label, error, fieldId, hint, className = '', ...rest },
  ref,
) {
  const describedBy = buildDescribedBy(fieldId, error, hint)

  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={fieldId}>{label}</label>
      <input
        ref={ref}
        id={fieldId}
        className={`${styles.input}${error ? ` ${styles.inputError}` : ''} ${className}`.trim()}
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
        {...rest}
      />
      {hint && !error ? <span id={`${fieldId}-hint`} className={styles.hint}>{hint}</span> : null}
      {error ? <span id={`${fieldId}-error`} className={styles.error} role="alert">{error}</span> : null}
    </div>
  )
})

/* ── SelectField ────────────────────────────────────────────────────────────── */

export type SelectFieldProps = FieldMeta & Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> & { children: ReactNode }

export const SelectField = forwardRef<HTMLSelectElement, SelectFieldProps>(function SelectField(
  { label, error, fieldId, hint, children, className = '', ...rest },
  ref,
) {
  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={fieldId}>{label}</label>
      <select
        ref={ref}
        id={fieldId}
        className={`${styles.select}${error ? ` ${styles.inputError}` : ''} ${className}`.trim()}
        aria-invalid={error ? true : undefined}
        {...rest}
      >
        {children}
      </select>
      {hint && !error ? <span className={styles.hint}>{hint}</span> : null}
      {error ? <span className={styles.error} role="alert">{error}</span> : null}
    </div>
  )
})

/* ── TextareaField ──────────────────────────────────────────────────────────── */

export type TextareaFieldProps = FieldMeta & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'id'>

export const TextareaField = forwardRef<HTMLTextAreaElement, TextareaFieldProps>(function TextareaField(
  { label, error, fieldId, hint, className = '', ...rest },
  ref,
) {
  const describedBy = buildDescribedBy(fieldId, error, hint)

  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={fieldId}>{label}</label>
      <textarea
        ref={ref}
        id={fieldId}
        className={`${styles.textarea}${error ? ` ${styles.inputError}` : ''} ${className}`.trim()}
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
        {...rest}
      />
      {hint && !error ? <span id={`${fieldId}-hint`} className={styles.hint}>{hint}</span> : null}
      {error ? <span id={`${fieldId}-error`} className={styles.error} role="alert">{error}</span> : null}
    </div>
  )
})

/* ── Helpers ────────────────────────────────────────────────────────────────── */

function buildDescribedBy(fieldId: string, error?: string, hint?: string): string | undefined {
  const ids = [
    error ? `${fieldId}-error` : null,
    hint && !error ? `${fieldId}-hint` : null,
  ].filter(Boolean)
  return ids.length > 0 ? ids.join(' ') : undefined
}
