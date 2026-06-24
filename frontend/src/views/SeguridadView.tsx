/**
 * SeguridadView — Security, governance, and HITL approvals.
 *
 * Three sub-areas:
 *   (a) Pending HITL approvals — polled every 3 s, Approve/Deny via MfaModal.
 *   (b) Governance — MFA enrollment + security policy presets + accordion catalog.
 *   (c) Security center — egress permissions, audit chain, recent scans.
 *
 * Design changes vs. previous version:
 *   - "Configuración avanzada" section REMOVED; capabilities are always an
 *     accordion (collapsed: name + count + delicacy chip + section toggle;
 *     expanded: full per-tool checkbox list).
 *   - Preset buttons show a PREVIEW only; a "Guardar" button triggers MfaModal.
 *   - All MFA collection goes through MfaModal — no inline input fields.
 *   - "Pedir mi MFA para los comandos peligrosos" requires MFA to DISABLE (ON is free).
 *   - When mfa_on_dangers is OFF, approvals and toggles fire directly.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { sileo } from 'sileo'
import {
  listPendingApprovals,
  mfaStatus,
  mfaSetRiddle,
  getPolicies,
  setPolicyPreset,
  setPolicyTool,
  setMfaOnDangers,
  getSecurityScans,
  getAuditChainHead,
  getSecurityPolicy,
  listEgressDomains,
  grantEgressDomain,
  revokeEgressDomain,
  recordInstallDecision,
} from '../api/client'
import type {
  PendingApproval,
  MfaStatus,
  PoliciesResponse,
  PolicyCatalogEntry,
  SecurityScan,
  AuditHead,
} from '../api/types'
import ApprovalCard from '../components/ApprovalCard'
import MfaEnroll from '../components/MfaEnroll'
import MfaModal from '../components/MfaModal'
import type { MfaTier, MfaFactors } from '../components/MfaModal'

// ── Approvals section ─────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 3000

function ApprovalsSection({ mfaDisabled }: { mfaDisabled: boolean }) {
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
      <div className="cv-section-label">Aprobaciones pendientes</div>
      {loading ? (
        <div className="cv-skeleton" aria-busy="true" aria-label="Cargando aprobaciones…" />
      ) : approvals.length === 0 ? (
        <div className="cv-empty">Sin aprobaciones pendientes.</div>
      ) : (
        <div className="cv-list">
          {approvals.map(a => (
            <ApprovalCard
              key={a.proposal_id}
              approval={a}
              mfaDisabled={mfaDisabled}
              onResolved={load}
            />
          ))}
        </div>
      )}
    </section>
  )
}

// ── Governance section ────────────────────────────────────────────────────────

const PRESETS: Array<[string, string, string]> = [
  ['equilibrado', 'Equilibrado', 'Todo activo salvo las acciones de mayor riesgo (recomendado)'],
  ['permisivo', 'Permisivo', 'Todo activo — el agente actúa sin restricciones, bajo tu responsabilidad'],
  ['bloqueado', 'Bloqueado', 'Todo desactivado — el agente no puede ejecutar ninguna acción'],
]

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
  // Legacy / catch-all mappings
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
  if (level === 'normal') return null
  const label = level === 'most_delicate' ? 'Muy delicado' : 'Delicado'
  const color = level === 'most_delicate' ? 'var(--danger)' : 'var(--warn)'
  return (
    <span
      className={`seg-pol-badge ${size === 'sm' ? 'seg-pol-badge--sm' : ''}`}
      style={{ color, background: `${color}22` }}
      aria-label={`Nivel de delicadeza: ${label}`}
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
  busy: boolean
  onToggleTool: (name: string, enabled: boolean) => void
  onToggleAll: (category: string, enabled: boolean, entries: PolicyCatalogEntry[]) => void
}

function CategoryGroup({
  category,
  entries,
  busy,
  onToggleTool,
  onToggleAll,
}: CategoryGroupProps) {
  const [expanded, setExpanded] = useState(false)

  const allOn = entries.every(e => e.enabled)
  const allOff = entries.every(e => !e.enabled)
  const mixed = !allOn && !allOff
  const delicacy = aggregateDelicacy(entries)
  const switchId = `cat-switch-${category}`
  const bodyId = `cat-body-${category}`

  return (
    <div className="seg-pol-group">
      <div className="seg-pol-group__header">
        <button
          type="button"
          className="seg-pol-group__expand"
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={() => setExpanded(v => !v)}
          title={expanded ? 'Contraer' : 'Expandir herramientas'}
        >
          <span className={`seg-pol-chevron ${expanded ? 'seg-pol-chevron--open' : ''}`} aria-hidden="true">▸</span>
        </button>

        <span className="seg-pol-group__name">{categoryLabel(category)}</span>
        <span className="seg-pol-group__count" aria-label={`${entries.length} herramientas`}>{entries.length}</span>

        <DelicacyBadge level={delicacy} />

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
          onChange={v => onToggleAll(category, v, entries)}
        />
      </div>

      {expanded && (
        <ul
          id={bodyId}
          className="seg-pol-tool-list"
          aria-label={`Herramientas de ${categoryLabel(category)}`}
        >
          {entries.map(entry => (
            <ToolRow
              key={entry.name}
              entry={entry}
              busy={busy}
              onToggle={onToggleTool}
            />
          ))}
        </ul>
      )}
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
  const checkId = `tool-${entry.name}`
  const tipId = `tool-tip-${entry.name}`
  const notVisible = !entry.llm_visible

  return (
    <li className={`seg-pol-tool-row ${notVisible ? 'seg-pol-tool-row--muted' : ''}`}>
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
          title="El agente usa el equivalente nativo; esta herramienta no aparece en el catálogo del LLM"
          aria-label="Usa equivalente nativo"
        >
          nativo
        </span>
      )}
    </li>
  )
}

// ── Pending MFA action ────────────────────────────────────────────────────────
// Represents an action queued to fire after MFA confirmation.

type PendingAction =
  | { kind: 'preset'; preset: string }
  | { kind: 'tool'; tool: string; enabled: boolean }
  | { kind: 'section'; category: string; enabled: boolean; entries: PolicyCatalogEntry[] }
  | { kind: 'mfa_dangers'; enabled: boolean }

// ── Governance section ────────────────────────────────────────────────────────

function GovernanceSection() {
  const [mfa, setMfa] = useState<MfaStatus | null>(null)
  const [pol, setPol] = useState<PoliciesResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [riddleQ, setRiddleQ] = useState('')
  const [riddleA, setRiddleA] = useState('')
  const [riddleTotp, setRiddleTotp] = useState('')
  const [busy, setBusy] = useState(false)

  // Preset preview: which preset is pending save (not yet applied)
  const [pendingPreset, setPendingPreset] = useState<string | null>(null)

  // MFA modal state: what action is waiting for factors
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null)

  const mfaDisabled = pol?.mfa_on_dangers === false

  const load = useCallback(async () => {
    const [m, p] = await Promise.all([mfaStatus(), getPolicies()])
    setMfa(m)
    setPol(p)
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const { capabilityGroups, defenseGroups } = useMemo(() => {
    const catalog = pol?.catalog ?? []
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
  }, [pol?.catalog])

  const legacyToolNames = useMemo(() => {
    if ((pol?.catalog?.length ?? 0) > 0) return []
    return Object.keys(pol?.tools ?? {}).sort()
  }, [pol?.catalog, pol?.tools])

  // Determine MFA tier needed for the current pending action.
  // Presets + mfa_dangers switch need riddle if enrolled; tools use mfa tier.
  function tierForAction(action: PendingAction): MfaTier {
    if (action.kind === 'preset' || action.kind === 'mfa_dangers') {
      return mfa?.riddle_set ? 'mfa_riddle' : 'mfa'
    }
    // Tool / section toggles: use the most delicate tier among the entries
    if (action.kind === 'tool') {
      const entry = pol?.catalog?.find(e => e.name === action.tool)
      if (entry?.delicacy === 'most_delicate') return 'mfa_riddle'
      if (entry?.delicacy === 'delicate') return mfa?.riddle_set ? 'mfa_riddle' : 'mfa_humanity'
      return 'mfa'
    }
    if (action.kind === 'section') {
      const delicacy = aggregateDelicacy(action.entries)
      if (delicacy === 'most_delicate') return 'mfa_riddle'
      if (delicacy === 'delicate') return mfa?.riddle_set ? 'mfa_riddle' : 'mfa_humanity'
      return 'mfa'
    }
    return 'mfa'
  }

  // Apply optimistic update for a single tool
  function applyOptimisticTool(toolName: string, enabled: boolean) {
    setPol(prev => {
      if (!prev) return prev
      return {
        ...prev,
        tools: { ...prev.tools, [toolName]: enabled },
        catalog: prev.catalog?.map(e => e.name === toolName ? { ...e, enabled } : e),
      }
    })
  }

  function revertOptimisticTool(toolName: string, enabled: boolean) {
    applyOptimisticTool(toolName, !enabled)
  }

  // Handle MFA sign callback from the modal
  async function handleSign(factors: MfaFactors) {
    if (!pendingAction) return
    setBusy(true)
    try {
      if (pendingAction.kind === 'preset') {
        await setPolicyPreset(
          pendingAction.preset,
          factors.totp,
          factors.riddle_answer ?? null,
        )
        sileo.success({ title: `Preset «${pendingAction.preset}» aplicado` })
        setPendingPreset(null)
        setPendingAction(null)
        await load()

      } else if (pendingAction.kind === 'tool') {
        applyOptimisticTool(pendingAction.tool, pendingAction.enabled)
        try {
          await setPolicyTool(pendingAction.tool, pendingAction.enabled, factors.totp, factors.riddle_answer ?? null)
          sileo.success({ title: `${pendingAction.tool}: ${pendingAction.enabled ? 'activado' : 'desactivado'}` })
          setPendingAction(null)
        } catch (err) {
          revertOptimisticTool(pendingAction.tool, pendingAction.enabled)
          sileo.error({ title: `No se pudo cambiar: ${err instanceof Error ? err.message : err}` })
          // Modal stays open on error (we set pendingAction=null only on success above)
          return
        }

      } else if (pendingAction.kind === 'section') {
        const toChange = pendingAction.entries.filter(e => e.enabled !== pendingAction.enabled)
        let firstError: string | null = null
        for (const entry of toChange) {
          try {
            await setPolicyTool(entry.name, pendingAction.enabled, factors.totp, factors.riddle_answer ?? null)
            applyOptimisticTool(entry.name, pendingAction.enabled)
          } catch (err) {
            firstError = err instanceof Error ? err.message : String(err)
            break
          }
        }
        if (firstError) {
          sileo.error({ title: `Error al cambiar la categoría: ${firstError}` })
          await load()
          return
        }
        if (toChange.length > 0) {
          sileo.success({ title: `Categoría ${pendingAction.enabled ? 'activada' : 'desactivada'}` })
        }
        setPendingAction(null)

      } else if (pendingAction.kind === 'mfa_dangers') {
        await setMfaOnDangers(pendingAction.enabled, factors.totp, factors.riddle_answer ?? null)
        sileo.success({
          title: pendingAction.enabled
            ? 'Verificación en peligrosos: activa'
            : 'Verificación en peligrosos: desactivada',
        })
        setPol(prev => prev ? { ...prev, mfa_on_dangers: pendingAction.enabled } : prev)
        setPendingAction(null)
      }
    } catch (err) {
      sileo.error({ title: `No se pudo aplicar: ${err instanceof Error ? err.message : err}` })
      // Leave modal open so user can retry with corrected TOTP
      return
    } finally {
      setBusy(false)
    }
  }

  // ── Handler wrappers that decide whether to open MFA modal or fire directly ──

  function requestPresetSave() {
    if (!pendingPreset) return
    if (mfaDisabled) {
      // Can't actually call setPolicyPreset without MFA — this path only exists
      // when mfa_on_dangers is false. Presets still need MFA (they're NORMAL+).
      // But per spec: when mfaDisabled, go direct. We'll let the server enforce.
      void handleSign({ totp: '' })
    } else {
      setPendingAction({ kind: 'preset', preset: pendingPreset })
    }
  }

  function requestToolToggle(tool: string, enabled: boolean) {
    if (mfaDisabled) {
      // MFA globally off — fire immediately with empty totp
      void (async () => {
        applyOptimisticTool(tool, enabled)
        try {
          await setPolicyTool(tool, enabled, '', null)
          sileo.success({ title: `${tool}: ${enabled ? 'activado' : 'desactivado'}` })
        } catch (err) {
          revertOptimisticTool(tool, enabled)
          sileo.error({ title: `No se pudo cambiar: ${err instanceof Error ? err.message : err}` })
        }
      })()
    } else {
      setPendingAction({ kind: 'tool', tool, enabled })
    }
  }

  function requestSectionToggle(category: string, enabled: boolean, entries: PolicyCatalogEntry[]) {
    if (mfaDisabled) {
      void (async () => {
        const toChange = entries.filter(e => e.enabled !== enabled)
        for (const entry of toChange) {
          try {
            await setPolicyTool(entry.name, enabled, '', null)
            applyOptimisticTool(entry.name, enabled)
          } catch (err) {
            sileo.error({ title: `No se pudo cambiar ${entry.name}: ${err instanceof Error ? err.message : err}` })
            await load()
            return
          }
        }
        if (toChange.length > 0) sileo.success({ title: `Categoría ${enabled ? 'activada' : 'desactivada'}` })
      })()
    } else {
      setPendingAction({ kind: 'section', category, enabled, entries })
    }
  }

  function requestMfaDangersToggle(checked: boolean) {
    if (checked) {
      // Turning ON is free (no MFA needed)
      void (async () => {
        setBusy(true)
        try {
          await setMfaOnDangers(true, '', null)
          sileo.success({ title: 'Verificación en peligrosos: activa' })
          setPol(prev => prev ? { ...prev, mfa_on_dangers: true } : prev)
        } catch (err) {
          sileo.error({ title: `No se pudo activar: ${err instanceof Error ? err.message : err}` })
        } finally {
          setBusy(false)
        }
      })()
    } else {
      // Turning OFF requires MFA
      setPendingAction({ kind: 'mfa_dangers', enabled: false })
    }
  }

  async function handleRiddleSave() {
    if (!riddleQ.trim() || !riddleA.trim() || !riddleTotp.trim()) {
      sileo.error({ title: 'Rellena pregunta, respuesta y código MFA' })
      return
    }
    try {
      await mfaSetRiddle(riddleTotp, riddleQ, riddleA)
      sileo.success({ title: 'Acertijo guardado' })
      setRiddleQ(''); setRiddleA(''); setRiddleTotp('')
      await load()
    } catch (err) {
      sileo.error({ title: `No se pudo guardar: ${err instanceof Error ? err.message : err}` })
    }
  }

  // Legacy tool toggle (flat tools map, no catalog)
  function requestLegacyToolToggle(toolName: string, enabled: boolean) {
    if (mfaDisabled) {
      void (async () => {
        try {
          await setPolicyTool(toolName, enabled, '', null)
          sileo.success({ title: `${toolName}: ${enabled ? 'activado' : 'desactivado'}` })
          setPol(prev => prev ? { ...prev, tools: { ...prev.tools, [toolName]: enabled } } : prev)
        } catch (err) {
          sileo.error({ title: `No se pudo cambiar: ${err instanceof Error ? err.message : err}` })
          setPol(prev => prev ? { ...prev, tools: { ...prev.tools, [toolName]: !enabled } } : prev)
        }
      })()
    } else {
      setPendingAction({ kind: 'tool', tool: toolName, enabled })
    }
  }

  if (loading) return <div className="cv-skeleton" aria-busy="true" aria-label="Cargando gobernanza…" />
  if (!mfa || !pol) return null

  const hasCatalog = (pol.catalog?.length ?? 0) > 0
  const currentPreset = pendingPreset ?? pol.preset
  const hasPendingPreset = pendingPreset !== null && pendingPreset !== pol.preset

  return (
    <>
      {/* ── MFA Modal ── */}
      {pendingAction && (
        <MfaModal
          tier={tierForAction(pendingAction)}
          title={
            pendingAction.kind === 'preset'
              ? `Aplicar preset «${pendingAction.preset}»`
              : pendingAction.kind === 'mfa_dangers'
              ? 'Desactivar verificación en peligrosos'
              : pendingAction.kind === 'section'
              ? `${pendingAction.enabled ? 'Activar' : 'Desactivar'} categoría`
              : `${pendingAction.enabled ? 'Activar' : 'Desactivar'} herramienta`
          }
          riddleQuestion={mfa.riddle_question}
          onSign={handleSign}
          onCancel={() => setPendingAction(null)}
        />
      )}

      {/* ── MFA enrollment ── */}
      <section className="cv-section">
        <div className="cv-section-label">Tu verificación (MFA)</div>
        <div className="seg-card">
          <p className="seg-card__intro">
            {mfa.enrolled
              ? 'MFA activo. Aprobar acciones peligrosas y cambiar políticas requiere tu código.'
              : 'Sin MFA no puedes aprobar acciones peligrosas. Actívalo con tu app de autenticación.'}
            {mfa.enrolled && (mfa.riddle_set
              ? ' Acertijo configurado.'
              : ' Falta tu acertijo (necesario para lo más delicado).')}
          </p>

          {!mfa.enrolled && <MfaEnroll onEnrolled={load} />}

          {mfa.enrolled && (
            <details className="seg-details">
              <summary>{mfa.riddle_set ? 'Cambiar' : 'Configurar'} acertijo personal</summary>
              <div className="seg-details__body">
                <input
                  className="cv-input"
                  placeholder="Pregunta (ej. ciudad donde nací)"
                  aria-label="Pregunta del acertijo"
                  value={riddleQ}
                  onChange={e => setRiddleQ(e.target.value)}
                />
                <input
                  className="cv-input"
                  placeholder="Respuesta"
                  aria-label="Respuesta del acertijo"
                  value={riddleA}
                  onChange={e => setRiddleA(e.target.value)}
                />
                <input
                  className="cv-input"
                  inputMode="numeric"
                  placeholder="Tu código MFA actual"
                  aria-label="Código MFA para guardar acertijo"
                  value={riddleTotp}
                  onChange={e => setRiddleTotp(e.target.value)}
                />
                <button className="cv-btn cv-btn--primary" onClick={handleRiddleSave} type="button">
                  Guardar acertijo
                </button>
              </div>
            </details>
          )}
        </div>
      </section>

      {/* ── Policies ── */}
      <section className="cv-section">
        <div className="cv-section-label">Políticas de seguridad — qué puede hacer el agente</div>
        <div className="seg-card">
          <p className="seg-card__intro">
            Cambiar cualquier política requiere tu código MFA (así el agente nunca abre su propia jaula).
          </p>

          {/* MFA on dangers global toggle */}
          <div className="seg-pol-danger-row">
            <div className="seg-pol-danger-row__info">
              <span className="seg-pol-danger-row__label">
                Pedir mi MFA para los comandos peligrosos
              </span>
              <span className="seg-pol-danger-row__hint">
                {mfaDisabled
                  ? 'Desactivado — el agente ejecuta acciones peligrosas sin pedirte confirmación.'
                  : 'Si lo desactivas, el agente ejecuta acciones peligrosas en autónomo sin pedírtelo. Recomendado mantenerlo activo.'}
              </span>
            </div>
            <ToggleSwitch
              id="toggle-mfa-dangers"
              aria-label="Pedir MFA para comandos peligrosos"
              checked={pol.mfa_on_dangers ?? true}
              disabled={busy}
              onChange={requestMfaDangersToggle}
            />
          </div>

          {/* Preset quick-access: preview + save */}
          <div>
            <div className="seg-pol-sub-label">Preset rápido</div>
            <div className="seg-presets">
              {PRESETS.map(([id, label, desc]) => (
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
              ))}
            </div>
            {hasPendingPreset && (
              <div className="seg-pol-preset-save-row" aria-live="polite">
                <span className="seg-pol-preset-hint">
                  Vista previa: «{pendingPreset}». Guarda para aplicarlo.
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
              </div>
            )}
          </div>

          {/* Capability accordion groups */}
          {hasCatalog && capabilityGroups.size > 0 && (
            <div>
              <div className="seg-pol-sub-label">Capacidades del agente</div>
              <div className="seg-pol-catalog">
                {[...capabilityGroups.entries()].map(([cat, entries]) => (
                  <CategoryGroup
                    key={cat}
                    category={cat}
                    entries={entries}
                    busy={busy}
                    onToggleTool={requestToolToggle}
                    onToggleAll={requestSectionToggle}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Defense tools accordion groups */}
          {hasCatalog && defenseGroups.size > 0 && (
            <div style={{ marginTop: 12 }}>
              <div className="seg-pol-sub-label">Defensas del sistema</div>
              <p className="seg-card__intro" style={{ marginTop: 0, marginBottom: 8 }}>
                Estas herramientas protegen el sistema. No son capacidades del agente — no las invoca directamente.
              </p>
              <div className="seg-pol-catalog">
                {[...defenseGroups.entries()].map(([cat, entries]) => (
                  <CategoryGroup
                    key={cat}
                    category={cat}
                    entries={entries}
                    busy={busy}
                    onToggleTool={requestToolToggle}
                    onToggleAll={requestSectionToggle}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Legacy flat list — shown only when catalog is absent */}
          {!hasCatalog && legacyToolNames.length > 0 && (
            <details className="seg-details" style={{ marginTop: 12 }}>
              <summary>Comandos uno a uno ({legacyToolNames.length})</summary>
              <div className="seg-tool-list">
                {legacyToolNames.map(name => (
                  <label key={name} className="seg-tool-row">
                    <input
                      type="checkbox"
                      checked={pol.tools?.[name] ?? false}
                      onChange={e => requestLegacyToolToggle(name, e.target.checked)}
                      aria-label={`Permiso para ${name}`}
                    />
                    <span>{name}</span>
                  </label>
                ))}
              </div>
            </details>
          )}
        </div>
      </section>
    </>
  )
}

// ── Egress section ────────────────────────────────────────────────────────────

function EgressSection() {
  const [domains, setDomains] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [input, setInput] = useState('')

  const loadDomains = useCallback(async () => {
    const res = await listEgressDomains()
    setDomains(res.domains ?? [])
    setLoading(false)
  }, [])

  useEffect(() => { loadDomains() }, [loadDomains])

  async function handleGrant() {
    const d = input.trim().toLowerCase()
    if (!d) return
    try {
      await grantEgressDomain(d)
      sileo.success({ title: `${d} autorizado` })
      setInput('')
      await loadDomains()
    } catch (err) {
      sileo.error({ title: `No se pudo autorizar: ${err instanceof Error ? err.message : err}` })
    }
  }

  async function handleRevoke(d: string) {
    try {
      await revokeEgressDomain(d)
      sileo.success({ title: `${d} revocado` })
      await loadDomains()
    } catch (err) {
      sileo.error({ title: `No se pudo revocar: ${err instanceof Error ? err.message : err}` })
    }
  }

  return (
    <section className="cv-section">
      <div className="cv-section-label">Permisos de red — dominios permitidos</div>
      <div className="seg-card">
        <p className="seg-card__intro">
          Por defecto el agente no puede acceder a ningún sitio web. Añade aquí los dominios
          a los que quieras darle acceso (p.ej. <code>pypi.org</code>, <code>github.com</code>).
          Aplica al navegador y al terminal del agente.
        </p>
        <div className="cv-form-inline">
          <input
            id="egress-domain-input"
            className="cv-input"
            type="text"
            placeholder="dominio (ej. github.com)"
            autoComplete="off"
            spellCheck={false}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleGrant() }}
            aria-label="Dominio a autorizar"
          />
          <button
            className="cv-btn cv-btn--primary"
            onClick={handleGrant}
            type="button"
          >
            Autorizar
          </button>
        </div>
        {loading ? (
          <div className="cv-skeleton" aria-busy="true" />
        ) : domains.length === 0 ? (
          <p className="cv-empty">Ningún dominio autorizado — el agente no accede a la red.</p>
        ) : (
          <ul className="cv-list" aria-label="Dominios autorizados">
            {domains.map(d => (
              <li key={d} className="seg-egress-row">
                <code className="seg-egress-row__domain">{d}</code>
                <button
                  className="cv-btn cv-btn--ghost cv-btn--sm"
                  onClick={() => handleRevoke(d)}
                  type="button"
                  aria-label={`Revocar dominio ${d}`}
                >
                  Revocar
                </button>
              </li>
            ))}
          </ul>
        )}
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
        riddle_answer: factors.riddle_answer?.trim() ?? null,
      })
      sileo.success({ title: 'Instalación permitida (decisión soberana, auditada). Reinténtala.' })
      setAllowed(true)
      setShowModal(false)
    } catch (err) {
      sileo.error({ title: `No se pudo permitir: ${err instanceof Error ? err.message : err}` })
      // Leave modal open for retry
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="seg-scan-row">
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
            PERMITIDO
          </span>
        )}
        {flagged && !allowed && scanId && (
          <button
            className="cv-btn cv-btn--ghost cv-btn--sm"
            onClick={() => setShowModal(true)}
            type="button"
            disabled={busy}
          >
            Permitir igualmente
          </button>
        )}
      </div>

      {showModal && (
        <MfaModal
          tier="mfa_riddle"
          title="Permitir instalación"
          onSign={handleAllow}
          onCancel={() => setShowModal(false)}
        />
      )}
    </div>
  )
}

// ── Security center section ───────────────────────────────────────────────────

function SecurityCenterSection() {
  const [scans, setScans] = useState<SecurityScan[] | null>(null)
  const [auditHead, setAuditHead] = useState<AuditHead | null | undefined>(undefined)
  const [policy, setPolicy] = useState<unknown>(undefined)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([getSecurityScans(), getAuditChainHead(), getSecurityPolicy()])
      .then(([s, a, p]) => {
        setScans(Array.isArray(s) ? s : [])
        setAuditHead(a)
        setPolicy(p)
      })
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return <div className="cv-skeleton" aria-busy="true" aria-label="Cargando centro de seguridad…" />
  }

  return (
    <>
      <section className="cv-section">
        <div className="cv-section-label">Cadena de auditoría</div>
        <div className="seg-card">
          {!auditHead ? (
            <p className="cv-empty">Sin datos de cadena de auditoría.</p>
          ) : (
            <div className="seg-audit-head">
              <code className="seg-audit-head__hash">
                {auditHead.hash ?? auditHead.head ?? '—'}
              </code>
              {auditHead.timestamp && (
                <span className="seg-audit-head__time">
                  {new Date(auditHead.timestamp).toLocaleString('es')}
                </span>
              )}
            </div>
          )}
        </div>
      </section>

      <section className="cv-section">
        <div className="cv-section-label">Escaneos recientes</div>
        {!scans || scans.length === 0 ? (
          <p className="cv-empty">Sin escaneos recientes.</p>
        ) : (
          <div className="cv-list">
            {scans.map((s, i) => (
              <ScanRow key={s.scan_id ?? s.id ?? i} scan={s} />
            ))}
          </div>
        )}
      </section>

      <section className="cv-section">
        <div className="cv-section-label">Política activa</div>
        <div className="seg-card">
          {policy == null ? (
            <p className="cv-empty">Sin política configurada.</p>
          ) : (
            <pre className="seg-policy-pre">
              {JSON.stringify(policy, null, 2)}
            </pre>
          )}
        </div>
      </section>
    </>
  )
}

// ── SeguridadView ─────────────────────────────────────────────────────────────

export default function SeguridadView() {
  // mfa_on_dangers state is needed both by GovernanceSection (toggle)
  // and ApprovalsSection (pass-through). We lift it here so both share it.
  const [mfaDisabled, setMfaDisabled] = useState(false)

  useEffect(() => {
    getPolicies().then(p => setMfaDisabled(p.mfa_on_dangers === false)).catch(() => {})
  }, [])

  return (
    <div className="cv-view-body">
      <div className="view-header" style={{ padding: 0, border: 'none' }}>
        <h1 className="view-title">Seguridad y gobernanza</h1>
        <p className="view-subtitle">
          Aprobaciones, políticas del agente, escaneos y cadena de auditoría.
        </p>
      </div>

      <ApprovalsSection mfaDisabled={mfaDisabled} />
      <GovernanceSection />
      <EgressSection />
      <SecurityCenterSection />
    </div>
  )
}
