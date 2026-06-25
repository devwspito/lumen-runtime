/**
 * SeguridadView — Security, governance, and HITL approvals.
 *
 * Three sub-areas:
 *   (a) Pending HITL approvals — polled every 3 s, Approve/Deny via MfaModal.
 *   (b) Governance — MFA enrollment + security policy presets + accordion catalog.
 *   (c) Security center — egress permissions, recent scans.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { sileo } from 'sileo'
import { Save } from 'lucide-react'
import { useT } from '../lib/i18n'
import {
  listPendingApprovals,
  mfaStatus,
  getPolicies,
  setPolicyPreset,
  setPolicyTools,
  setMfaOnDangers,
  getSecurityScans,
  grantEgressDomain,
  revokeEgressDomain,
  getEgressMode,
  setEgressMode,
  blockEgressDomain,
  unblockEgressDomain,
  recordInstallDecision,
} from '../api/client'
import type { EgressMode, EgressModeResponse } from '../api/types'
import type {
  PendingApproval,
  MfaStatus,
  PoliciesResponse,
  PolicyCatalogEntry,
  SecurityScan,
} from '../api/types'
import ApprovalCard from '../components/ApprovalCard'
import MfaEnroll from '../components/MfaEnroll'
import MfaModal from '../components/MfaModal'
import type { MfaFactors } from '../components/MfaModal'
import {
  AnimatePresence,
  AnimatedListItem,
  AnimatedExpanderContent,
  AnimatedChevron,
  Stagger,
  StaggerItem,
  HoverRow,
  motion,
  SPRING,
  TWEEN,
} from '../components/ui/motion'

// ── Approvals section ─────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 3000

function ApprovalsSection({ mfaDisabled }: { mfaDisabled: boolean }) {
  const t = useT()
  const [approvals, setApprovals] = useState<PendingApproval[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    const data = await listPendingApprovals()
    setApprovals(Array.isArray(data) ? data : [])
    setLoading(false)
  }, [])

  useEffect(() => {
    load()
    const timer = setInterval(load, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [load])

  return (
    <section className="cv-section">
      <div className="cv-section-label">{t('seg.approvals.label')}</div>
      {loading ? (
        <div className="cv-skeleton" aria-busy="true" aria-label="Cargando…" />
      ) : approvals.length === 0 ? (
        <div className="cv-empty">{t('seg.approvals.empty')}</div>
      ) : (
        <div className="cv-list">
          <AnimatePresence initial={false}>
            {approvals.map(a => (
              <AnimatedListItem key={a.proposal_id}>
                <ApprovalCard
                  approval={a}
                  mfaDisabled={mfaDisabled}
                  onResolved={load}
                />
              </AnimatedListItem>
            ))}
          </AnimatePresence>
        </div>
      )}
    </section>
  )
}

// ── Governance section ────────────────────────────────────────────────────────

// PRESETS are defined as a function so they can use the translator.
// The tuple is [id, label] — descriptions come from t() in the component.
const PRESET_IDS: Array<[string, string]> = [
  ['equilibrado', 'Equilibrado'],
  ['permisivo',   'Permisivo'],
  ['bloqueado',   'Bloqueado'],
]

/**
 * Mirror of the backend's _preset_default (tool_policy.py:224-229).
 * Returns what `enabled` would be for a given tool under the target preset,
 * before any per-tool overrides.  Used only for client-side preview.
 *
 * EQUILIBRADO: off only for most_delicate tools (those that require explicit
 * owner opt-in). Everything else is on.
 */
function presetPreviewEnabled(entry: PolicyCatalogEntry, preset: string): boolean {
  if (preset === 'permisivo') return true
  if (preset === 'bloqueado') return false
  // equilibrado: disabled only for most_delicate
  return entry.delicacy !== 'most_delicate'
}

const CATEGORY_LABELS: Record<string, string> = {
  apps:          'Apps',
  web:           'Web y navegador',
  communication: 'Comunicación',
  screen:        'Pantalla y control',
  composio:      'Apps conectadas',
  system:        'Sistema',
  orchestration: 'Orquestación',
  terminal:      'Terminal',
  media:         'Medios',
  mcp:           'Herramientas externas (MCP)',
  programming:   'Programación',
  filesystem:    'Ficheros',
  memory:        'Memoria',
  network:       'Red',
  browser:       'Navegador',
  tasks:         'Tareas programadas',
  agents:        'Agentes',
  providers:     'Modelos y proveedores',
  security:      'Seguridad del sistema',
}

const DEFENSE_CATEGORIES = new Set(['security'])

function categoryLabel(cat: string): string {
  return CATEGORY_LABELS[cat] ?? cat.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase())
}

type DelicacyLevel = 'normal' | 'delicate' | 'most_delicate'

function aggregateDelicacy(entries: PolicyCatalogEntry[]): DelicacyLevel {
  if (entries.some(e => e.delicacy === 'most_delicate')) return 'most_delicate'
  if (entries.some(e => e.delicacy === 'delicate')) return 'delicate'
  return 'normal'
}

function DelicacyBadge({ level, size = 'normal' }: { level: DelicacyLevel; size?: 'normal' | 'sm' }) {
  const t = useT()
  if (level === 'normal') return null
  const label = level === 'most_delicate' ? t('seg.badge.approval') : t('seg.badge.attention')
  const color = level === 'most_delicate' ? 'var(--danger)' : 'var(--warn)'
  return (
    <span
      className={`seg-pol-badge ${size === 'sm' ? 'seg-pol-badge--sm' : ''}`}
      style={{ color, background: `${color}22` }}
      aria-label={label}
    >
      {label}
    </span>
  )
}

interface ToggleSwitchProps {
  id: string
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  indeterminate?: boolean
  'aria-label': string
}

function ToggleSwitch({ id, checked, onChange, disabled, indeterminate, 'aria-label': ariaLabel }: ToggleSwitchProps) {
  return (
    <button
      id={id}
      role="switch"
      type="button"
      aria-checked={indeterminate ? 'mixed' : checked}
      aria-label={ariaLabel}
      disabled={disabled}
      className={`seg-pol-switch ${checked && !indeterminate ? 'seg-pol-switch--on' : ''} ${indeterminate ? 'seg-pol-switch--mixed' : ''}`}
      onClick={() => onChange(!checked)}
    />
  )
}

// ── Accordion category group ──────────────────────────────────────────────────

interface CategoryGroupProps {
  category: string
  entries: PolicyCatalogEntry[]
  /** Overrides for local pending changes (tool name → enabled). */
  pendingChanges: Record<string, boolean>
  busy: boolean
  onToggleTool: (name: string, enabled: boolean) => void
  onToggleAll: (category: string, enabled: boolean, entries: PolicyCatalogEntry[]) => void
}

function CategoryGroup({
  category,
  entries,
  pendingChanges,
  busy,
  onToggleTool,
  onToggleAll,
}: CategoryGroupProps) {
  const [expanded, setExpanded] = useState(false)

  // Merge committed state with local pending changes
  const effectiveEntries = entries.map(e =>
    e.name in pendingChanges ? { ...e, enabled: pendingChanges[e.name] } : e,
  )

  const allOn = effectiveEntries.every(e => e.enabled)
  const allOff = effectiveEntries.every(e => !e.enabled)
  const mixed = !allOn && !allOff
  const delicacy = aggregateDelicacy(entries)
  const switchId = `cat-switch-${category}`
  const bodyId = `cat-body-${category}`

  return (
    <div className="seg-pol-group">
      {/* Entire header row is clickable to toggle expand/collapse */}
      <HoverRow
        className="seg-pol-group__header seg-pol-group__header--clickable"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        aria-controls={bodyId}
        onClick={() => setExpanded(v => !v)}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded(v => !v) } }}
      >
        <AnimatedChevron open={expanded} size={13} />

        <span className="seg-pol-group__name">{categoryLabel(category)}</span>
        <span className="seg-pol-group__count" aria-label={`${entries.length} herramientas`}>{entries.length}</span>

        <DelicacyBadge level={delicacy} />

        {/* Stop propagation so the toggle switch doesn't also expand/collapse */}
        <div
          onClick={e => e.stopPropagation()}
          onKeyDown={e => e.stopPropagation()}
          style={{ display: 'contents' }}
        >
          <label className="seg-pol-group__toggle-label" htmlFor={switchId}>
            <span className="sr-only">
              {allOn ? 'Todo activo' : allOff ? 'Todo desactivado' : 'Parcialmente activo'} — activar o desactivar toda la categoría
            </span>
          </label>
          <ToggleSwitch
            id={switchId}
            aria-label={`Activar o desactivar todas las herramientas de ${categoryLabel(category)}`}
            checked={allOn}
            indeterminate={mixed}
            disabled={busy}
            onChange={v => onToggleAll(category, v, effectiveEntries)}
          />
        </div>
      </HoverRow>

      {/* AnimatedExpanderContent replaces the conditional render — smooth height transition */}
      <AnimatedExpanderContent open={expanded}>
        <ul
          id={bodyId}
          className="seg-pol-tool-list"
          aria-label={`Herramientas de ${categoryLabel(category)}`}
        >
          {effectiveEntries.map(entry => (
            <ToolRow
              key={entry.name}
              entry={entry}
              busy={busy}
              onToggle={onToggleTool}
            />
          ))}
        </ul>
      </AnimatedExpanderContent>
    </div>
  )
}

// ── Tool row ──────────────────────────────────────────────────────────────────

interface ToolRowProps {
  entry: PolicyCatalogEntry
  busy: boolean
  onToggle: (name: string, enabled: boolean) => void
}

function ToolRow({ entry, busy, onToggle }: ToolRowProps) {
  const t = useT()
  const checkId = `tool-${entry.name}`
  const tipId = `tool-tip-${entry.name}`
  const notVisible = !entry.llm_visible

  return (
    <motion.li
      className={`seg-pol-tool-row ${notVisible ? 'seg-pol-tool-row--muted' : ''}`}
      whileHover={{ x: 2 }}
      transition={SPRING}
    >
      <input
        type="checkbox"
        id={checkId}
        aria-describedby={notVisible ? tipId : undefined}
        checked={entry.enabled}
        disabled={busy}
        onChange={e => onToggle(entry.name, e.target.checked)}
        className="seg-pol-tool-check"
        aria-label={`${entry.label}: ${entry.enabled ? 'activo' : 'inactivo'}`}
      />
      <label htmlFor={checkId} className="seg-pol-tool-label">
        {entry.label}
      </label>
      <DelicacyBadge level={entry.delicacy} size="sm" />
      {notVisible && (
        <span
          id={tipId}
          className="seg-pol-tool-native"
          title={t('seg.tool.native.tip')}
          aria-label={t('seg.tool.native.label')}
        >
          {t('seg.tool.native.label')}
        </span>
      )}
    </motion.li>
  )
}

// ── Pending MFA action ────────────────────────────────────────────────────────

type PendingAction =
  | { kind: 'preset'; preset: string }
  | { kind: 'batch'; changes: Record<string, boolean> }
  | { kind: 'mfa_dangers'; enabled: boolean }

// ── Governance section ────────────────────────────────────────────────────────

function GovernanceSection() {
  const t = useT()
  const [mfa, setMfa] = useState<MfaStatus | null>(null)
  const [pol, setPol] = useState<PoliciesResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  // Preset preview: which preset is pending save (not yet applied)
  const [pendingPreset, setPendingPreset] = useState<string | null>(null)

  // MFA modal state: what action is waiting for factors
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null)

  // Local tool overrides accumulate until "Guardar cambios" is clicked
  const [toolPending, setToolPending] = useState<Record<string, boolean>>({})
  const hasPendingTools = Object.keys(toolPending).length > 0

  const mfaDisabled = pol?.mfa_on_dangers === false

  const load = useCallback(async () => {
    const [m, p] = await Promise.all([mfaStatus(), getPolicies()])
    setMfa(m)
    setPol(p)
    setLoading(false)
    // Clear any local pending state on reload so we don't show stale overrides
    setToolPending({})
  }, [])

  useEffect(() => { load() }, [load])

  const { capabilityGroups, defenseGroups } = useMemo(() => {
    const rawCatalog = pol?.catalog ?? []
    // When a preset is pending (user clicked a preset button but hasn't saved yet),
    // project the catalog's `enabled` fields through the preset preview so the accordion
    // shows what will happen after save — not the stale committed state.
    const previewPreset = pendingPreset && pendingPreset !== pol?.preset ? pendingPreset : null
    const catalog = previewPreset
      ? rawCatalog.map(e => ({ ...e, enabled: presetPreviewEnabled(e, previewPreset) }))
      : rawCatalog

    const grouped = new Map<string, PolicyCatalogEntry[]>()
    for (const entry of catalog) {
      const list = grouped.get(entry.category) ?? []
      list.push(entry)
      grouped.set(entry.category, list)
    }
    const capability: Map<string, PolicyCatalogEntry[]> = new Map()
    const defense: Map<string, PolicyCatalogEntry[]> = new Map()
    for (const [cat, entries] of grouped) {
      if (DEFENSE_CATEGORIES.has(cat)) {
        defense.set(cat, entries)
      } else {
        capability.set(cat, entries)
      }
    }
    return { capabilityGroups: capability, defenseGroups: defense }
  }, [pol?.catalog, pol?.preset, pendingPreset])

  const legacyToolNames = useMemo(() => {
    if ((pol?.catalog?.length ?? 0) > 0) return []
    return Object.keys(pol?.tools ?? {}).sort()
  }, [pol?.catalog, pol?.tools])

  // Persist a batch of tool changes directly (no MFA modal needed).
  async function persistBatchDirect(changes: Record<string, boolean>) {
    setBusy(true)
    // Optimistic local update
    setPol(prev => {
      if (!prev) return prev
      const updatedTools = { ...prev.tools, ...changes }
      const updatedCatalog = prev.catalog?.map(e =>
        e.name in changes ? { ...e, enabled: changes[e.name] } : e,
      )
      return { ...prev, tools: updatedTools, catalog: updatedCatalog }
    })
    setToolPending({})
    try {
      await setPolicyTools(changes, '')
      sileo.success({ title: t('seg.save.ok') })
      await load()
    } catch (err) {
      await load()
      sileo.error({ title: t('seg.save.err').replace('{err}', err instanceof Error ? err.message : String(err)) })
    } finally {
      setBusy(false)
    }
  }

  // Handle MFA sign callback from the modal
  async function handleSign(factors: MfaFactors) {
    if (!pendingAction) return
    setBusy(true)
    try {
      if (pendingAction.kind === 'preset') {
        await setPolicyPreset(pendingAction.preset, factors.totp)
        sileo.success({ title: t('seg.preset.ok').replace('{preset}', pendingAction.preset) })
        setPendingPreset(null)
        setPendingAction(null)
        await load()

      } else if (pendingAction.kind === 'batch') {
        // Optimistic: apply locally first
        setPol(prev => {
          if (!prev) return prev
          const updatedTools = { ...prev.tools, ...pendingAction.changes }
          const updatedCatalog = prev.catalog?.map(e =>
            e.name in pendingAction.changes ? { ...e, enabled: pendingAction.changes[e.name] } : e,
          )
          return { ...prev, tools: updatedTools, catalog: updatedCatalog }
        })
        setToolPending({})
        try {
          await setPolicyTools(pendingAction.changes, factors.totp)
          sileo.success({ title: t('seg.save.ok') })
          setPendingAction(null)
          await load()
        } catch (err) {
          // Revert optimistic update
          await load()
          sileo.error({ title: t('seg.save.err').replace('{err}', err instanceof Error ? err.message : String(err)) })
          return
        }

      } else if (pendingAction.kind === 'mfa_dangers') {
        await setMfaOnDangers(pendingAction.enabled, factors.totp)
        sileo.success({
          title: pendingAction.enabled
            ? t('seg.dangers.on.ok')
            : t('seg.dangers.off.ok'),
        })
        setPol(prev => prev ? { ...prev, mfa_on_dangers: pendingAction.enabled } : prev)
        setPendingAction(null)
      }
    } catch (err) {
      sileo.error({ title: t('seg.preset.err').replace('{err}', err instanceof Error ? err.message : String(err)) })
      return
    } finally {
      setBusy(false)
    }
  }

  // ── Handler wrappers ─────────────────────────────────────────────────────────

  function requestPresetSave() {
    if (!pendingPreset) return
    if (mfaDisabled) {
      // No verification required: persist directly without opening the modal
      setBusy(true)
      void setPolicyPreset(pendingPreset, '')
        .then(() => {
          sileo.success({ title: t('seg.preset.ok').replace('{preset}', pendingPreset) })
          setPendingPreset(null)
          return load()
        })
        .catch(err => {
          sileo.error({ title: t('seg.preset.err').replace('{err}', err instanceof Error ? err.message : String(err)) })
        })
        .finally(() => setBusy(false))
    } else {
      setPendingAction({ kind: 'preset', preset: pendingPreset })
    }
  }

  // Individual tool toggle: update local pending only (no immediate API call)
  function requestToolToggle(tool: string, enabled: boolean) {
    setToolPending(prev => ({ ...prev, [tool]: enabled }))
  }

  // Category toggle: batch-update all tools in the category locally
  function requestSectionToggle(_category: string, enabled: boolean, entries: PolicyCatalogEntry[]) {
    const updates: Record<string, boolean> = {}
    for (const entry of entries) {
      if (entry.enabled !== enabled) {
        updates[entry.name] = enabled
      }
    }
    if (Object.keys(updates).length > 0) {
      setToolPending(prev => ({ ...prev, ...updates }))
    }
  }

  // Commit the batch of pending tool changes.
  // MFA enabled → open one MfaModal which calls handleSign on confirm.
  // MFA disabled → persist directly, no modal.
  function handleSaveToolChanges() {
    if (!hasPendingTools) return
    if (mfaDisabled) {
      void persistBatchDirect({ ...toolPending })
    } else {
      setPendingAction({ kind: 'batch', changes: { ...toolPending } })
    }
  }

  function requestMfaDangersToggle(checked: boolean) {
    if (checked) {
      void (async () => {
        setBusy(true)
        try {
          await setMfaOnDangers(true, '')
          sileo.success({ title: t('seg.dangers.on.ok') })
          setPol(prev => prev ? { ...prev, mfa_on_dangers: true } : prev)
        } catch (err) {
          sileo.error({ title: t('seg.preset.err').replace('{err}', err instanceof Error ? err.message : String(err)) })
        } finally {
          setBusy(false)
        }
      })()
    } else {
      setPendingAction({ kind: 'mfa_dangers', enabled: false })
    }
  }

  // Legacy tool toggle (flat tools map, no catalog)
  function requestLegacyToolToggle(toolName: string, enabled: boolean) {
    setToolPending(prev => ({ ...prev, [toolName]: enabled }))
  }

  if (loading) return <div className="cv-skeleton" aria-busy="true" aria-label="Cargando gobernanza…" />
  if (!mfa || !pol) return null

  const hasCatalog = (pol.catalog?.length ?? 0) > 0
  const currentPreset = pendingPreset ?? pol.preset
  const hasPendingPreset = pendingPreset !== null && pendingPreset !== pol.preset

  // When batch modal is open, use the already-captured changes snapshot
  const batchChanges = pendingAction?.kind === 'batch' ? pendingAction.changes : toolPending

  return (
    <>
      {/* ── Verification Modal — kept outside Stagger so it renders above all sections */}
      {pendingAction && (
        <MfaModal
          title={
            pendingAction.kind === 'preset'
              ? t('seg.mfa_modal.preset').replace('{preset}', pendingAction.preset)
              : pendingAction.kind === 'mfa_dangers'
              ? t('seg.mfa_modal.dangers_off')
              : t('seg.mfa_modal.tools')
          }
          onSign={handleSign}
          onCancel={() => {
            setPendingAction(null)
            // Restore toolPending from batch snapshot so user can adjust before retrying
            if (pendingAction?.kind === 'batch') {
              setToolPending(batchChanges)
            }
          }}
        />
      )}

      {/* ── Two-step verification enrollment ── */}
      <section className="cv-section">
        <div className="cv-section-label">{t('seg.mfa.label')}</div>
        <div className="seg-card">
          <p className="seg-card__intro">
            {mfa.enrolled
              ? t('seg.mfa.enrolled')
              : t('seg.mfa.not_enrolled')}
          </p>

          {!mfa.enrolled && <MfaEnroll onEnrolled={load} />}
        </div>
      </section>

      {/* ── Permissions ── */}
      <section className="cv-section">
        <div className="cv-section-label">{t('seg.policies.label')}</div>
        <div className="seg-card">
          <p className="seg-card__intro">
            {t('seg.policies.intro')}
          </p>

          {/* Verification on sensitive actions toggle */}
          <div className="seg-pol-danger-row">
            <div className="seg-pol-danger-row__info">
              <span className="seg-pol-danger-row__label">
                {t('seg.policies.dangers.label')}
              </span>
              <span className="seg-pol-danger-row__hint">
                {mfaDisabled
                  ? t('seg.policies.dangers.off')
                  : t('seg.policies.dangers.on')}
              </span>
            </div>
            <ToggleSwitch
              id="toggle-mfa-dangers"
              aria-label={t('seg.policies.dangers.label')}
              checked={pol.mfa_on_dangers ?? true}
              disabled={busy}
              onChange={requestMfaDangersToggle}
            />
          </div>

          {/* Preset quick-access: preview + save */}
          <div>
            <div className="seg-pol-sub-label">Preset rápido</div>
            <div className="seg-presets">
              {PRESET_IDS.map(([id, label]) => {
                const desc = t(
                  id === 'equilibrado' ? 'seg.preset.equilibrado.desc'
                  : id === 'permisivo' ? 'seg.preset.permisivo.desc'
                  : 'seg.preset.bloqueado.desc'
                )
                return (
                  <button
                    key={id}
                    className={`cv-btn cv-btn--sm ${currentPreset === id ? 'cv-btn--primary' : 'cv-btn--secondary'}`}
                    title={desc}
                    onClick={() => setPendingPreset(id)}
                    type="button"
                    disabled={busy}
                    aria-pressed={currentPreset === id}
                  >
                    {label}
                  </button>
                )
              })}
            </div>

            {/* Animated preset-save bar — slides in when a preset is pending */}
            <AnimatePresence initial={false}>
              {hasPendingPreset && (
                <motion.div
                  className="seg-pol-preset-save-row"
                  aria-live="polite"
                  initial={{ opacity: 0, y: -6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={TWEEN}
                >
                  <span className="seg-pol-preset-hint">
                    {t('seg.policies.preset.hint').replace('{preset}', pendingPreset ?? '')}
                  </span>
                  <button
                    type="button"
                    className="cv-btn cv-btn--primary cv-btn--sm"
                    onClick={requestPresetSave}
                    disabled={busy}
                  >
                    Guardar
                  </button>
                  <button
                    type="button"
                    className="cv-btn cv-btn--ghost cv-btn--sm"
                    onClick={() => setPendingPreset(null)}
                    disabled={busy}
                  >
                    Cancelar
                  </button>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Capability accordion groups — staggered entrance */}
          {hasCatalog && capabilityGroups.size > 0 && (
            <div>
              <div className="seg-pol-sub-label">Capacidades del agente</div>
              <Stagger className="seg-pol-catalog">
                {[...capabilityGroups.entries()].map(([cat, entries]) => (
                  <StaggerItem key={cat}>
                    <CategoryGroup
                      category={cat}
                      entries={entries}
                      pendingChanges={toolPending}
                      busy={busy}
                      onToggleTool={requestToolToggle}
                      onToggleAll={requestSectionToggle}
                    />
                  </StaggerItem>
                ))}
              </Stagger>

              {/* Animated "Guardar cambios" bar — slides in when tools are pending */}
              <AnimatePresence initial={false}>
                {hasPendingTools && (
                  <motion.div
                    className="seg-pol-preset-save-row"
                    aria-live="polite"
                    initial={{ opacity: 0, y: -6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -6 }}
                    transition={TWEEN}
                  >
                    <span className="seg-pol-preset-hint">
                      {Object.keys(toolPending).length} cambio{Object.keys(toolPending).length !== 1 ? 's' : ''} pendiente{Object.keys(toolPending).length !== 1 ? 's' : ''}.
                    </span>
                    <button
                      type="button"
                      className="cv-btn cv-btn--primary cv-btn--sm"
                      onClick={handleSaveToolChanges}
                      disabled={busy}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--sp-1)' }}
                    >
                      <Save size={14} aria-hidden="true" />
                      Guardar cambios
                    </button>
                    <button
                      type="button"
                      className="cv-btn cv-btn--ghost cv-btn--sm"
                      onClick={() => setToolPending({})}
                      disabled={busy}
                    >
                      Descartar
                    </button>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )}

          {/* Defense tools accordion groups */}
          {hasCatalog && defenseGroups.size > 0 && (
            <div style={{ marginTop: 12 }}>
              <div className="seg-pol-sub-label">Defensas del sistema</div>
              <p className="seg-card__intro" style={{ marginTop: 0, marginBottom: 8 }}>
                Estas herramientas protegen el sistema. No son capacidades del agente — no las invoca directamente.
              </p>
              <Stagger className="seg-pol-catalog">
                {[...defenseGroups.entries()].map(([cat, entries]) => (
                  <StaggerItem key={cat}>
                    <CategoryGroup
                      category={cat}
                      entries={entries}
                      pendingChanges={toolPending}
                      busy={busy}
                      onToggleTool={requestToolToggle}
                      onToggleAll={requestSectionToggle}
                    />
                  </StaggerItem>
                ))}
              </Stagger>
            </div>
          )}

          {/* Legacy flat list — shown only when catalog is absent */}
          {!hasCatalog && legacyToolNames.length > 0 && (
            <>
              <details className="seg-details" style={{ marginTop: 12 }}>
                <summary>Comandos uno a uno ({legacyToolNames.length})</summary>
                <div className="seg-tool-list">
                  {legacyToolNames.map(name => {
                    const effective = name in toolPending ? toolPending[name] : (pol.tools?.[name] ?? false)
                    return (
                      <label key={name} className="seg-tool-row">
                        <input
                          type="checkbox"
                          checked={effective}
                          onChange={e => requestLegacyToolToggle(name, e.target.checked)}
                          aria-label={`Permiso para ${name}`}
                        />
                        <span>{name}</span>
                      </label>
                    )
                  })}
                </div>
              </details>

              {/* Animated "Guardar cambios" bar for legacy flat list */}
              <AnimatePresence initial={false}>
                {hasPendingTools && (
                  <motion.div
                    className="seg-pol-preset-save-row"
                    aria-live="polite"
                    initial={{ opacity: 0, y: -6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -6 }}
                    transition={TWEEN}
                  >
                    <span className="seg-pol-preset-hint">
                      {Object.keys(toolPending).length} cambio{Object.keys(toolPending).length !== 1 ? 's' : ''} pendiente{Object.keys(toolPending).length !== 1 ? 's' : ''}.
                    </span>
                    <button
                      type="button"
                      className="cv-btn cv-btn--primary cv-btn--sm"
                      onClick={handleSaveToolChanges}
                      disabled={busy}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--sp-1)' }}
                    >
                      <Save size={14} aria-hidden="true" />
                      Guardar cambios
                    </button>
                    <button
                      type="button"
                      className="cv-btn cv-btn--ghost cv-btn--sm"
                      onClick={() => setToolPending({})}
                      disabled={busy}
                    >
                      Descartar
                    </button>
                  </motion.div>
                )}
              </AnimatePresence>
            </>
          )}
        </div>
      </section>
    </>
  )
}

// ── Egress section ────────────────────────────────────────────────────────────

// ── Mode toggle pill ──────────────────────────────────────────────────────────

interface EgressModeToggleProps {
  mode: EgressMode
  busy: boolean
  onRequest: (next: EgressMode) => void
}

function EgressModeToggle({ mode, busy, onRequest }: EgressModeToggleProps) {
  const t = useT()
  return (
    <div className="seg-egress-mode-toggle" role="group" aria-label={t('seg.network.mode.label')}>
      <button
        type="button"
        className={`seg-egress-mode-toggle__btn ${mode === 'allow' ? 'seg-egress-mode-toggle__btn--active' : ''}`}
        aria-pressed={mode === 'allow'}
        disabled={busy || mode === 'allow'}
        onClick={() => onRequest('allow')}
      >
        {t('seg.network.allow')}
      </button>
      <button
        type="button"
        className={`seg-egress-mode-toggle__btn ${mode === 'deny' ? 'seg-egress-mode-toggle__btn--active seg-egress-mode-toggle__btn--deny' : ''}`}
        aria-pressed={mode === 'deny'}
        disabled={busy || mode === 'deny'}
        onClick={() => onRequest('deny')}
      >
        {t('seg.network.deny')}
      </button>
    </div>
  )
}

// ── Allow-mode panel (manual block-list) ──────────────────────────────────────

interface AllowModeProps {
  denyList: string[]
  blocklistCount: number | undefined
  onAdd: (domain: string) => Promise<void>
  onRemove: (domain: string) => Promise<void>
}

function AllowModePanel({ denyList, blocklistCount, onAdd, onRemove }: AllowModeProps) {
  const t = useT()
  const [input, setInput] = useState('')

  async function handleAdd() {
    const d = input.trim().toLowerCase()
    if (!d) return
    await onAdd(d)
    setInput('')
  }

  return (
    <motion.div
      key="allow-panel"
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -6 }}
      transition={TWEEN}
    >
      <p className="seg-card__intro">
        {t('seg.network.allow.intro')}
      </p>

      {blocklistCount != null && blocklistCount > 0 && (
        <div className="seg-egress-system-badge" aria-label={`${blocklistCount} sitios maliciosos bloqueados por el sistema`}>
          <span className="seg-egress-system-badge__dot" aria-hidden="true" />
          {blocklistCount} sitios maliciosos bloqueados por el sistema
        </div>
      )}

      <div className="seg-pol-sub-label" style={{ marginTop: 'var(--sp-3)' }}>
        Sitios bloqueados manualmente
      </div>

      <div className="cv-form-inline" style={{ marginBottom: 'var(--sp-2)' }}>
        <input
          id="egress-block-input"
          className="cv-input"
          type="text"
          placeholder="dominio (ej. ejemplo.com)"
          autoComplete="off"
          spellCheck={false}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { void handleAdd() } }}
          aria-label="Dominio a bloquear"
        />
        <button
          className="cv-btn cv-btn--secondary"
          onClick={() => { void handleAdd() }}
          type="button"
          disabled={!input.trim()}
        >
          Bloquear
        </button>
      </div>

      {denyList.length === 0 ? (
        <p className="cv-empty">{t('seg.network.none_blocked')}</p>
      ) : (
        <ul className="cv-list" aria-label="Dominios bloqueados manualmente">
          <AnimatePresence initial={false}>
            {denyList.map(d => (
              <AnimatedListItem key={d} className="seg-egress-row">
                <code className="seg-egress-row__domain">{d}</code>
                <button
                  className="cv-btn cv-btn--ghost cv-btn--sm"
                  onClick={() => { void onRemove(d) }}
                  type="button"
                  aria-label={`Desbloquear dominio ${d}`}
                >
                  Desbloquear
                </button>
              </AnimatedListItem>
            ))}
          </AnimatePresence>
        </ul>
      )}
    </motion.div>
  )
}

// ── Deny-mode panel (allow-list) ──────────────────────────────────────────────

interface DenyModeProps {
  allowList: string[]
  onGrant: (domain: string) => Promise<void>
  onRevoke: (domain: string) => Promise<void>
}

function DenyModePanel({ allowList, onGrant, onRevoke }: DenyModeProps) {
  const t = useT()
  const [input, setInput] = useState('')

  async function handleGrant() {
    const d = input.trim().toLowerCase()
    if (!d) return
    await onGrant(d)
    setInput('')
  }

  return (
    <motion.div
      key="deny-panel"
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -6 }}
      transition={TWEEN}
    >
      <p className="seg-card__intro">
        {t('seg.network.deny.intro')}
      </p>

      <div className="cv-form-inline" style={{ marginBottom: 'var(--sp-2)' }}>
        <input
          id="egress-grant-input"
          className="cv-input"
          type="text"
          placeholder="dominio (ej. tu-erp.empresa.com)"
          autoComplete="off"
          spellCheck={false}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { void handleGrant() } }}
          aria-label="Dominio a autorizar"
        />
        <button
          className="cv-btn cv-btn--primary"
          onClick={() => { void handleGrant() }}
          type="button"
          disabled={!input.trim()}
        >
          Autorizar
        </button>
      </div>

      {allowList.length === 0 ? (
        <p className="cv-empty">{t('seg.network.none_allowed')}</p>
      ) : (
        <ul className="cv-list" aria-label="Dominios autorizados">
          <AnimatePresence initial={false}>
            {allowList.map(d => (
              <AnimatedListItem key={d} className="seg-egress-row">
                <code className="seg-egress-row__domain">{d}</code>
                <button
                  className="cv-btn cv-btn--ghost cv-btn--sm"
                  onClick={() => { void onRevoke(d) }}
                  type="button"
                  aria-label={`${t('seg.network.revoke')} ${d}`}
                >
                  {t('seg.network.revoke')}
                </button>
              </AnimatedListItem>
            ))}
          </AnimatePresence>
        </ul>
      )}
    </motion.div>
  )
}

// ── EgressSection ─────────────────────────────────────────────────────────────

function EgressSection() {
  const t = useT()
  const [state, setState] = useState<EgressModeResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  // Verification modal: holds the requested next mode while waiting for TOTP
  const [pendingMode, setPendingMode] = useState<EgressMode | null>(null)

  const load = useCallback(async () => {
    const res = await getEgressMode()
    setState(res)
    setLoading(false)
  }, [])

  useEffect(() => { void load() }, [load])

  // ── Mode change (requires MFA) ──────────────────────────────────────────────

  function requestModeChange(next: EgressMode) {
    setPendingMode(next)
  }

  async function handleModeSign(factors: MfaFactors) {
    if (!pendingMode || !state) return
    const prev = state
    // Optimistic update
    setState(s => s ? { ...s, mode: pendingMode } : s)
    setPendingMode(null)
    setBusy(true)
    try {
      await setEgressMode(pendingMode, factors.totp)
      sileo.success({
        title: pendingMode === 'allow'
          ? t('seg.allow_mode.ok')
          : t('seg.deny_mode.ok'),
      })
      await load()
    } catch (err) {
      // Revert
      setState(prev)
      sileo.error({ title: t('seg.save.err').replace('{err}', err instanceof Error ? err.message : String(err)) })
    } finally {
      setBusy(false)
    }
  }

  // ── Allow-list (DENY mode) ─────────────────────────────────────────────────

  async function handleGrant(domain: string) {
    if (!state) return
    const prev = state
    setState(s => s ? { ...s, domains: [...s.domains, domain] } : s)
    try {
      await grantEgressDomain(domain)
      sileo.success({ title: `${domain} autorizado` })
      await load()
    } catch (err) {
      setState(prev)
      sileo.error({ title: `No se pudo autorizar: ${err instanceof Error ? err.message : err}` })
    }
  }

  async function handleRevoke(domain: string) {
    if (!state) return
    const prev = state
    setState(s => s ? { ...s, domains: s.domains.filter(d => d !== domain) } : s)
    try {
      await revokeEgressDomain(domain)
      sileo.success({ title: `${domain} revocado` })
      await load()
    } catch (err) {
      setState(prev)
      sileo.error({ title: `No se pudo revocar: ${err instanceof Error ? err.message : err}` })
    }
  }

  // ── Block-list (ALLOW mode) ────────────────────────────────────────────────

  async function handleBlock(domain: string) {
    if (!state) return
    const prev = state
    setState(s => s ? { ...s, deny: [...s.deny, domain] } : s)
    try {
      await blockEgressDomain(domain)
      sileo.success({ title: `${domain} bloqueado` })
      await load()
    } catch (err) {
      setState(prev)
      sileo.error({ title: `No se pudo bloquear: ${err instanceof Error ? err.message : err}` })
    }
  }

  async function handleUnblock(domain: string) {
    if (!state) return
    const prev = state
    setState(s => s ? { ...s, deny: s.deny.filter(d => d !== domain) } : s)
    try {
      await unblockEgressDomain(domain)
      sileo.success({ title: `${domain} desbloqueado` })
      await load()
    } catch (err) {
      setState(prev)
      sileo.error({ title: `No se pudo desbloquear: ${err instanceof Error ? err.message : err}` })
    }
  }

  return (
    <section className="cv-section">
      <div className="cv-section-label">{t('seg.network.label')}</div>

      {/* Verification modal for mode change — rendered outside the card so it layers above */}
      {pendingMode && (
        <MfaModal
          title={pendingMode === 'allow' ? t('seg.allow_mode.ok') : t('seg.deny_mode.ok')}
          onSign={handleModeSign}
          onCancel={() => setPendingMode(null)}
        />
      )}

      <div className="seg-card">
        {loading ? (
          <div className="cv-skeleton" aria-busy="true" aria-label="Cargando…" />
        ) : state != null ? (
          <>
            {/* Mode toggle */}
            <div className="seg-egress-mode-row">
              <div className="seg-egress-mode-row__info">
                <span className="seg-egress-mode-row__label">{t('seg.network.mode.label')}</span>
                <span className="seg-egress-mode-row__hint">
                  {t('seg.network.mode.hint')}
                </span>
              </div>
              <EgressModeToggle
                mode={state.mode}
                busy={busy}
                onRequest={requestModeChange}
              />
            </div>

            {/* Panel content transitions between ALLOW and DENY */}
            <AnimatePresence mode="wait" initial={false}>
              {state.mode === 'allow' ? (
                <AllowModePanel
                  key="allow"
                  denyList={state.deny ?? []}
                  blocklistCount={state.blocklist_count}
                  onAdd={handleBlock}
                  onRemove={handleUnblock}
                />
              ) : (
                <DenyModePanel
                  key="deny"
                  allowList={state.domains ?? []}
                  onGrant={handleGrant}
                  onRevoke={handleRevoke}
                />
              )}
            </AnimatePresence>
          </>
        ) : null}
      </div>
    </section>
  )
}

// ── Severity badge ────────────────────────────────────────────────────────────

function SeverityBadge({ severity }: { severity: string }) {
  const map: Record<string, [string, string]> = {
    critical: ['#FF453A', 'CRÍTICO'],
    high:     ['#FF8C00', 'ALTO'],
    medium:   ['#F5B945', 'MEDIO'],
    low:      ['#34D399', 'BAJO'],
    info:     ['#9A9AA2', 'INFO'],
  }
  const [color, label] = map[severity.toLowerCase()] ?? ['#9A9AA2', severity.toUpperCase()]
  return (
    <span
      className="seg-severity-badge"
      style={{ color, background: `${color}22` }}
    >
      {label}
    </span>
  )
}

// ── Scan row ──────────────────────────────────────────────────────────────────

function ScanRow({ scan }: { scan: SecurityScan }) {
  const t = useT()
  const [showModal, setShowModal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [allowed, setAllowed] = useState(
    String(scan.decision ?? '').toUpperCase() === 'ALLOWED'
  )

  const verdict = String(scan.verdict ?? '').toUpperCase()
  const sev = String(scan.severity ?? '').toLowerCase()
  const flagged = verdict === 'FAIL' || verdict === 'WARN' || sev === 'critical' || sev === 'high'
  const scanId = scan.scan_id ?? scan.id

  const name = scan.name ?? scan.identifier ?? scan.scan_id ?? 'Escaneo'
  const target = scan.target ?? scan.identifier

  async function handleAllow(factors: MfaFactors) {
    setBusy(true)
    try {
      await recordInstallDecision({
        scan_id: scanId!,
        decision: 'allow',
        identifier: scan.identifier ?? scan.target ?? '',
        kind: scan.kind ?? '',
        score: scan.score ?? -1,
        verdict: verdict || '',
        risks_json: '[]',
        totp: factors.totp.trim(),
      })
      sileo.success({ title: 'Instalación permitida (decisión soberana, auditada). Reinténtala.' })
      setAllowed(true)
      setShowModal(false)
    } catch (err) {
      sileo.error({ title: `No se pudo permitir: ${err instanceof Error ? err.message : err}` })
    } finally {
      setBusy(false)
    }
  }

  return (
    <HoverRow className="seg-scan-row">
      <div className="seg-scan-row__left">
        <div className="seg-scan-row__name">{name}</div>
        {target && <div className="seg-scan-row__target">{target}</div>}
      </div>
      <div className="seg-scan-row__right">
        {scan.severity && <SeverityBadge severity={scan.severity} />}
        {scan.score != null && (
          <span className="seg-score">{scan.score}</span>
        )}
        {allowed && (
          <span className="seg-severity-badge" style={{ color: '#34D399', background: '#34D39922' }}>
            {t('seg.scan.allowed')}
          </span>
        )}
        {flagged && !allowed && scanId && (
          <button
            className="cv-btn cv-btn--ghost cv-btn--sm"
            onClick={() => setShowModal(true)}
            type="button"
            disabled={busy}
          >
            {t('seg.scan.allow')}
          </button>
        )}
      </div>

      {showModal && (
        <MfaModal
          title="Permitir instalación"
          onSign={handleAllow}
          onCancel={() => setShowModal(false)}
        />
      )}
    </HoverRow>
  )
}

// ── Security center section ───────────────────────────────────────────────────

function SecurityCenterSection() {
  const t = useT()
  const [scans, setScans] = useState<SecurityScan[] | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getSecurityScans()
      .then(s => { setScans(Array.isArray(s) ? s : []) })
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return <div className="cv-skeleton" aria-busy="true" aria-label="Cargando…" />
  }

  return (
    <section className="cv-section">
      <div className="cv-section-label">{t('seg.scans.label')}</div>
      {!scans || scans.length === 0 ? (
        <p className="cv-empty">{t('seg.scans.empty')}</p>
      ) : (
        <div className="cv-list">
          <AnimatePresence initial={false}>
            {scans.map((s, i) => (
              <AnimatedListItem key={s.scan_id ?? s.id ?? i}>
                <ScanRow scan={s} />
              </AnimatedListItem>
            ))}
          </AnimatePresence>
        </div>
      )}
    </section>
  )
}

// ── SeguridadView ─────────────────────────────────────────────────────────────

export default function SeguridadView() {
  const [mfaDisabled, setMfaDisabled] = useState(false)

  useEffect(() => {
    getPolicies().then(p => setMfaDisabled(p.mfa_on_dangers === false)).catch(() => {})
  }, [])

  return (
    <div className="cv-view-body">
      <div className="view-header" style={{ padding: 0, border: 'none' }}>
        <h1 className="view-title">Seguridad y gobernanza</h1>
        <p className="view-subtitle">
          Aprobaciones, políticas del agente y escaneos de seguridad.
        </p>
      </div>

      <ApprovalsSection mfaDisabled={mfaDisabled} />
      <GovernanceSection />
      <EgressSection />
      <SecurityCenterSection />
    </div>
  )
}
