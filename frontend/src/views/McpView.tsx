import { useEffect, useReducer, useRef, useState } from 'react'
import { sileo } from 'sileo'
import { X, Terminal, Search, Wrench } from 'lucide-react'
import { listMcpServers, addMcpServer, removeMcpServer, searchMcpRegistry, scanInstall, recordSecurityDecision, ApiError } from '../api/client'
import type { McpServer, McpRegistryEntry, InstallScanResponse } from '../api/types'
import { useConfirmDialog } from '../components/ConfirmDialog'
import Badge from '../components/Badge'
import InstallScanModal from '../components/InstallScanModal'
import type { MfaFactors } from '../components/MfaModal'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { Button } from '../components/ui/Button'
import {
  AnimatePresence,
  AnimatedListItem,
  AnimatedExpanderContent,
  AnimatedChevron,
  FadeIn,
  Stagger,
  StaggerItem,
  HoverRow,
  motion,
  SPRING,
  TWEEN_FAST,
} from '../components/ui/motion'

// Curated catalog — mirrors mcp.js MCP_CATALOG (npx-only verified servers).
const MCP_CATALOG: McpRegistryEntry[] = [
  {
    server_id: 'github',
    label: 'GitHub',
    tag: 'Dev',
    description: 'Acceso a tus repositorios, issues y pull requests de GitHub.',
    argv: ['npx', '-y', '@modelcontextprotocol/server-github'],
    repository: 'https://github.com/github/github-mcp-server',
  },
  {
    server_id: 'context7',
    label: 'Context7',
    tag: 'Docs',
    description: 'Documentación de librerías en vivo, siempre actualizada.',
    argv: ['npx', '-y', '@upstash/context7-mcp'],
    repository: 'https://github.com/upstash/context7',
  },
  {
    server_id: 'filesystem',
    label: 'Archivos locales',
    tag: 'Sistema',
    description: 'Lee y escribe ficheros locales. Cada acción requiere tu permiso.',
    argv: ['npx', '-y', '@modelcontextprotocol/server-filesystem', '/var/lib/hermes/workspace'],
    repository: 'https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem',
  },
]

function slugify(name: string): string {
  return String(name || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'herramienta'
}

function getRunner(argv: string | string[] | undefined): string {
  const arr = Array.isArray(argv)
    ? argv
    : String(argv ?? '').split(/\s+/).filter(Boolean)
  return (arr[0] ? String(arr[0]) : '')
    .split(/[/\s]+/)
    .filter(Boolean)
    .pop() ?? ''
}

// Resolve the FETCHABLE npm coordinate ("npm:@scope/pkg") from an npx argv, so the
// security scan can download + statically analyse the ACTUAL package. Without this the
// scan only sees the display name (no registry coordinate) → PackageContentScanner has
// nothing to fetch → every MCP gets the same constant score. Returns null for a non-npx
// runner or a local/inline argv (no published package to fetch).
function npmCoordinateFromArgv(argv: string | string[] | undefined): string | null {
  const arr = Array.isArray(argv)
    ? argv
    : String(argv ?? '').split(/\s+/).filter(Boolean)
  if (!arr.length || getRunner(arr) !== 'npx') return null
  for (let i = 1; i < arr.length; i++) {
    const tok = arr[i]!
    if (tok.startsWith('-')) continue            // skip flags (-y, --yes, ...)
    if (/[/\\]/.test(tok) && !tok.startsWith('@')) return null  // local path, not a pkg
    return `npm:${tok}`                          // [@scope/]name[@version]
  }
  return null
}

// EnvField schema derived from entry.env_vars
interface EnvFieldSchema {
  key: string
  label: string
  required: boolean
  secret: boolean
}

function parseEnvSchema(entry: McpRegistryEntry): EnvFieldSchema[] {
  const rawVars = entry.env_vars ?? []
  return rawVars.map(v =>
    typeof v === 'string'
      ? { key: v, label: v, required: false, secret: true }
      : { key: v.key, label: v.label ?? v.key, required: Boolean(v.required), secret: Boolean(v.secret ?? true) },
  )
}

// ── State ─────────────────────────────────────────────────────────────────────

type State =
  | { status: 'loading' }
  | { status: 'success'; servers: McpServer[] }
  | { status: 'error'; message: string }

type Action =
  | { type: 'LOADING' }
  | { type: 'LOADED'; servers: McpServer[] }
  | { type: 'FAILED'; message: string }

function reducer(_s: State, a: Action): State {
  switch (a.type) {
    case 'LOADING': return { status: 'loading' }
    case 'LOADED': return { status: 'success', servers: a.servers }
    case 'FAILED': return { status: 'error', message: a.message }
  }
}

// Registry search — separate discriminated state so the main list stays intact
type RegistryState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; results: McpRegistryEntry[] }
  | { status: 'error'; message: string }

function show(message: string, kind: 'ok' | 'warn' | 'error' = 'ok', durationMs = 4000) {
  if (kind === 'ok') sileo.success({ title: message, duration: durationMs })
  else if (kind === 'error') sileo.error({ title: message, duration: durationMs })
  else sileo.warning({ title: message, duration: durationMs })
}

// Pending install approval: holds the scan result + pending install entry
interface PendingInstall {
  scan: InstallScanResponse
  entry: McpRegistryEntry
  collectedEnv: Record<string, string>
  onDone: () => void
}

export default function McpView() {
  const [state, dispatch] = useReducer(reducer, { status: 'loading' })
  const [registryState, setRegistryState] = useState<RegistryState>({ status: 'idle' })
  const [pendingInstall, setPendingInstall] = useState<PendingInstall | null>(null)
  const regInputRef = useRef<HTMLInputElement>(null)
  const [confirm, ConfirmDialogNode] = useConfirmDialog()

  function load() {
    dispatch({ type: 'LOADING' })
    listMcpServers()
      // Ruflo is a first-class Lumen integration, not a user-managed tool set.
      // The backend already hides it but we filter defensively client-side too.
      .then(servers => dispatch({ type: 'LOADED', servers: servers.filter(s => s.slug !== 'ruflo') }))
      .catch((e: unknown) => dispatch({
        type: 'FAILED',
        message: e instanceof ApiError ? e.message : 'No se pudieron cargar las herramientas externas.',
      }))
  }

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const installedIds = state.status === 'success'
    ? new Set(state.servers.map(s => s.server_id ?? s.id ?? ''))
    : new Set<string>()

  async function doAddMcpServer(entry: McpRegistryEntry, collectedEnv: Record<string, string>, onDone: () => void) {
    const argv = Array.isArray(entry.argv)
      ? entry.argv
      : String(entry.argv ?? '').split(/\s+/).filter(Boolean)

    try {
      const res = await addMcpServer({
        server_id: entry.server_id ?? entry.id ?? slugify(entry.name ?? ''),
        label: entry.label ?? entry.name,
        argv,
        env: { ...collectedEnv },
      })
      const name = entry.label ?? entry.name ?? ''
      if (res && res.tool_count === 0) {
        show(`"${name}" se conectó pero no tiene herramientas disponibles. Revisa su configuración.`, 'warn', 7000)
      } else {
        show(`"${name}" añadida — tus agentes ya pueden usarla`, 'ok')
      }
      load()
    } catch (e) {
      show(e instanceof Error ? e.message : 'Error', 'error')
    } finally {
      onDone()
    }
  }

  async function installEntry(entry: McpRegistryEntry, collectedEnv: Record<string, string>, onDone: () => void) {
    const runner = getRunner(entry.argv)
    if (runner && runner !== 'npx') {
      show(`Solo se admiten herramientas npx por ahora (esta usa ${runner}).`, 'warn', 7000)
      onDone()
      return
    }

    const identifier = entry.server_id ?? entry.id ?? slugify(entry.name ?? '')
    // Scan the FETCHABLE coordinate (npm:@scope/pkg) when we can resolve it, so the
    // content scanner downloads + analyses the real package and the verdict is REAL
    // (a malicious package -> FAIL, a clean one -> PASS) instead of a constant per-kind
    // score. Falls back to the display identifier if the argv isn't a published package.
    const scanTarget = npmCoordinateFromArgv(entry.argv) ?? identifier

    try {
      const scan = await scanInstall('mcp', scanTarget)
      // WARN and FAIL always route through the approval modal so the owner can
      // review and confirm with TOTP — no silent toast degradation.
      if (scan.requires_owner_approval || scan.verdict === 'WARN' || scan.verdict === 'FAIL') {
        setPendingInstall({ scan, entry, collectedEnv, onDone })
        return
      }
      // PASS → proceed directly
      await doAddMcpServer(entry, collectedEnv, onDone)
    } catch {
      // Scan endpoint unavailable — fall back to direct install
      await doAddMcpServer(entry, collectedEnv, onDone)
    }
  }

  async function handleScanApprove(factors: MfaFactors) {
    if (!pendingInstall) return
    const { scan, entry, collectedEnv, onDone } = pendingInstall
    setPendingInstall(null)
    try {
      await recordSecurityDecision({
        scan_id: scan.scan_id,
        decision: 'approve',
        identifier: scan.identifier ?? entry.server_id ?? entry.id ?? '',
        kind: 'mcp',
        score: scan.score,
        verdict: scan.verdict,
        risks_json: JSON.stringify(scan.risks),
        totp: factors.totp,
      })
      await doAddMcpServer(entry, collectedEnv, onDone)
    } catch (e) {
      show(e instanceof Error ? e.message : 'Error al registrar la decisión', 'error')
      onDone()
    }
  }

  async function searchRegistry() {
    const q = regInputRef.current?.value.trim() ?? ''
    if (q.length < 2) return
    setRegistryState({ status: 'loading' })
    try {
      const results = await searchMcpRegistry(q)
      const arr = Array.isArray(results) ? results : []
      setRegistryState({ status: 'success', results: arr })
    } catch (e) {
      setRegistryState({
        status: 'error',
        message: e instanceof ApiError ? e.message : 'No se pudo buscar en el registro.',
      })
    }
  }

  return (
    <>
      {ConfirmDialogNode}
      {pendingInstall && (
        <InstallScanModal
          scan={pendingInstall.scan}
          name={pendingInstall.entry.label ?? pendingInstall.entry.name ?? pendingInstall.scan.identifier ?? ''}
          onApprove={handleScanApprove}
          onCancel={() => {
            pendingInstall.onDone()
            setPendingInstall(null)
          }}
        />
      )}
      <PageHeader
        title="Herramientas externas"
        subtitle="Conecta conjuntos de herramientas externos para ampliar las capacidades del agente."
      />

      <div className="view-body cv-view-body">
        <Stagger style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-8)' }}>

          {/* ── Active servers ──────────────────────────────────────────────── */}
          <StaggerItem>
            <section className="cv-section" aria-label="Herramientas activas">
              <h2 className="cv-section-label">Activas</h2>
              {state.status === 'loading' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }} aria-busy="true">
                  {[...Array(2)].map((_, i) => <div key={i} className="cv-skeleton" style={{ height: 48 }} />)}
                </div>
              )}
              {state.status === 'error' && (
                <FadeIn>
                  <div role="alert">
                    <p className="state-error">{state.message}</p>
                    <Button variant="secondary" size="sm" onClick={load} style={{ marginTop: 8 }}>Reintentar</Button>
                  </div>
                </FadeIn>
              )}
              {state.status === 'success' && (
                state.servers.length === 0
                  ? (
                    <EmptyState
                      icon={<Wrench size={36} />}
                      title="Sin herramientas conectadas"
                      description="Añade una del catálogo sugerido o busca en el registro."
                    />
                  )
                  : (
                    <ul className="cv-list" role="list">
                      <AnimatePresence initial={false}>
                        {state.servers.map(s => (
                          <AnimatedListItem key={s.server_id ?? s.id}>
                            <McpServerRow
                              server={s}
                              onRemove={async () => {
                                const name = s.label ?? s.server_id ?? ''
                                const ok = await confirm({
                                  title: `¿Eliminar "${name}"?`,
                                  description: 'El agente dejará de tener acceso a estas herramientas.',
                                  confirmLabel: 'Eliminar',
                                  variant: 'danger',
                                })
                                if (!ok) return
                                try {
                                  await removeMcpServer(s.server_id ?? s.id ?? '')
                                  show('Conjunto de herramientas eliminado', 'ok')
                                  load()
                                } catch (e) {
                                  show(e instanceof Error ? e.message : 'Error', 'error')
                                }
                              }}
                            />
                          </AnimatedListItem>
                        ))}
                      </AnimatePresence>
                    </ul>
                  )
              )}
            </section>
          </StaggerItem>

          {/* ── Suggested catalog ───────────────────────────────────────────── */}
          <StaggerItem>
            <section className="cv-section" aria-label="Herramientas sugeridas">
              <h2 className="cv-section-label">Sugeridas</h2>
              <ul className="cv-list" role="list">
                <AnimatePresence initial={false}>
                  {MCP_CATALOG.map(entry => (
                    <AnimatedListItem key={entry.server_id}>
                      <CatalogCard
                        entry={entry}
                        installedIds={installedIds}
                        onInstall={installEntry}
                      />
                    </AnimatedListItem>
                  ))}
                </AnimatePresence>
              </ul>
            </section>
          </StaggerItem>

          {/* ── Official registry search ─────────────────────────────────── */}
          <StaggerItem>
            <section className="cv-section" aria-label="Buscar más herramientas">
              <h2 className="cv-section-label">Buscar más herramientas</h2>
              <div className="cv-search-row">
                <label className="sr-only" htmlFor="mcp-registry-input">Buscar herramientas externas</label>
                <div style={{ position: 'relative', flex: 1 }}>
                  <Search size={14} aria-hidden="true" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--ink4)', pointerEvents: 'none' }} />
                  <input
                    id="mcp-registry-input"
                    ref={regInputRef}
                    className="cv-input"
                    type="search"
                    placeholder="github, slack, postgres…"
                    autoComplete="off"
                    onKeyDown={e => { if (e.key === 'Enter') searchRegistry() }}
                    style={{ paddingLeft: 30 }}
                  />
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={searchRegistry}
                  loading={registryState.status === 'loading'}
                >
                  Buscar
                </Button>
              </div>
              <p className="cv-hint">Conectado al registro oficial de herramientas externas</p>
              {registryState.status === 'error' && (
                <div role="alert">
                  <p className="state-error">{registryState.message}</p>
                  <Button variant="secondary" size="sm" onClick={searchRegistry} style={{ marginTop: 8 }}>
                    Reintentar
                  </Button>
                </div>
              )}
              {registryState.status === 'success' && registryState.results.length > 0 && (
                <ul className="cv-list" role="list">
                  <AnimatePresence initial={false}>
                    {registryState.results.map((entry, i) => (
                      <AnimatedListItem key={`${entry.server_id ?? entry.id ?? entry.name ?? i}`}>
                        <CatalogCard
                          entry={entry}
                          installedIds={installedIds}
                          onInstall={installEntry}
                        />
                      </AnimatedListItem>
                    ))}
                  </AnimatePresence>
                </ul>
              )}
              {registryState.status === 'success' && registryState.results.length === 0 && (
                <EmptyState
                  icon={<Search size={32} />}
                  title="Sin resultados"
                  description="Prueba con otro término de búsqueda."
                />
              )}
            </section>
          </StaggerItem>

          {/* ── Manual add ──────────────────────────────────────────────────── */}
          <StaggerItem>
            <section className="cv-section" aria-label="Añadir manualmente">
              <h2 className="cv-section-label">Añadir manualmente</h2>
              <AddMcpForm onAdded={() => { show('Herramienta añadida — tus agentes ya pueden usarla', 'ok'); load() }} onToast={show} />
            </section>
          </StaggerItem>

        </Stagger>
      </div>
    </>
  )
}

// ── Active server row ─────────────────────────────────────────────────────────

interface McpServerRowProps {
  server: McpServer
  onRemove: () => void
}

function McpServerRow({ server, onRemove }: McpServerRowProps) {
  const [showCmd, setShowCmd] = useState(false)
  const argv = Array.isArray(server.argv) ? server.argv.join(' ') : (server.argv ?? '')
  const healthy = String(server.health ?? '').toLowerCase() === 'healthy'
  const hasHealth = server.health != null && server.health !== ''
  const tools = server.tool_count != null ? `${server.tool_count} herramienta${server.tool_count === 1 ? '' : 's'}` : ''

  return (
    <HoverRow className="mcp-row">
      <span style={{ color: 'var(--ink4)', flexShrink: 0, display: 'flex' }} aria-hidden="true">
        <Terminal size={15} />
      </span>
      <div className="mcp-row__info">
        <div className="mcp-row__name">
          {server.label ?? server.server_id ?? 'Herramienta externa'}
          {hasHealth && (
            <Badge variant={healthy ? 'ok' : 'danger'}>
              {tools || String(server.health)}
            </Badge>
          )}
          {!hasHealth && tools && <Badge variant="neutral">{tools}</Badge>}
        </div>
        {argv && (
          <button
            type="button"
            className="mcp-row__cmd"
            style={{
              cursor: 'pointer', background: 'none', border: 'none', padding: '2px 0',
              display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--ink4)',
              fontFamily: 'inherit',
            }}
            onClick={() => setShowCmd(v => !v)}
            aria-expanded={showCmd}
          >
            <AnimatedChevron open={showCmd} size={11} />
            <span style={{ fontSize: 'var(--text-caption)' }}>Detalles técnicos</span>
          </button>
        )}
        <AnimatedExpanderContent open={showCmd && Boolean(argv)}>
          <div className="mcp-row__cmd" style={{ marginTop: 2, paddingLeft: 16, userSelect: 'all' }}>
            {argv}
          </div>
        </AnimatedExpanderContent>
      </div>
      <button
        className="cv-btn cv-btn--ghost cv-btn--sm cv-btn--danger"
        onClick={onRemove}
        aria-label={`Eliminar ${server.label ?? 'herramienta externa'}`}
      >
        <X size={14} aria-hidden="true" />
      </button>
    </HoverRow>
  )
}

// ── Catalog / registry card ───────────────────────────────────────────────────

interface CatalogCardProps {
  entry: McpRegistryEntry
  installedIds: Set<string>
  onInstall: (entry: McpRegistryEntry, env: Record<string, string>, onDone: () => void) => void
}

function CatalogCard({ entry, installedIds, onInstall }: CatalogCardProps) {
  const [installing, setInstalling] = useState(false)
  const [showEnvForm, setShowEnvForm] = useState(false)
  const [envValues, setEnvValues] = useState<Record<string, string>>({})
  const id = entry.server_id ?? entry.id ?? slugify(entry.name ?? '')
  const already = installedIds.has(id) || installedIds.has(entry.server_id ?? '')
  const runner = getRunner(entry.argv)
  const nonNpx = runner !== '' && runner !== 'npx'
  const unsupported = entry.installable === false || nonNpx
  const envSchema = parseEnvSchema(entry)
  const needsEnv = envSchema.length > 0
  const repo = entry.repository ?? entry.homepage ?? entry.website ?? ''

  function handleInstallClick() {
    if (needsEnv) {
      setShowEnvForm(true)
    } else {
      setInstalling(true)
      onInstall(entry, {}, () => setInstalling(false))
    }
  }

  function handleEnvSubmit() {
    // Validate required fields
    for (const field of envSchema) {
      if (field.required && !(envValues[field.key] ?? '').trim()) {
        show(`"${field.label}" es obligatorio`, 'warn')
        return
      }
    }
    setShowEnvForm(false)
    setInstalling(true)
    onInstall(entry, { ...envValues }, () => setInstalling(false))
  }

  return (
    <motion.div
      className="mcp-row"
      style={{ flexDirection: 'column', alignItems: 'stretch', gap: 'var(--sp-2)' }}
      whileHover={{ y: -2 }}
      transition={SPRING}
      layout
    >
      {/* Main row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-3)' }}>
        <span style={{ color: 'var(--ink4)', flexShrink: 0, display: 'flex' }} aria-hidden="true">
          <Terminal size={15} />
        </span>
        <div className="mcp-row__info">
          <div className="mcp-row__name">
            {entry.label ?? entry.name ?? id}
            {entry.tag && <Badge variant="neutral">{entry.tag}</Badge>}
            {needsEnv && <Badge variant="warn">Requiere clave API</Badge>}
          </div>
          {entry.description && (
            <div className="mcp-row__cmd" style={{ color: 'var(--ink3)', fontFamily: 'var(--font-ui)' }}>
              {entry.description}
            </div>
          )}
          {unsupported && (entry.unsupported_reason || nonNpx) && (
            <div className="mcp-row__cmd" style={{ color: 'var(--warn)' }}>
              {entry.unsupported_reason ?? `Solo se admiten herramientas npx por ahora (esta usa ${runner}).`}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', flexShrink: 0 }}>
          {repo && (
            <a
              href={repo}
              target="_blank"
              rel="noopener noreferrer"
              className="cv-link cv-btn--sm"
            >
              Docs
            </a>
          )}
          {!showEnvForm && (
            <Button
              variant="secondary"
              size="sm"
              disabled={already || unsupported}
              loading={installing}
              onClick={handleInstallClick}
            >
              {already ? 'Añadida' : unsupported ? 'No disponible' : 'Añadir'}
            </Button>
          )}
        </div>
      </div>

      {/* Inline key-entry form */}
      <AnimatedExpanderContent open={showEnvForm}>
        <div className="cv-form-stack" style={{ paddingLeft: 27 }}>
          {envSchema.map(field => (
            <div key={field.key}>
              <label className="cv-label" htmlFor={`mcp-env-${id}-${field.key}`}>
                {field.label}{field.required ? ' *' : ''}
              </label>
              <input
                id={`mcp-env-${id}-${field.key}`}
                className="cv-input"
                type={field.secret ? 'password' : 'text'}
                autoComplete="off"
                value={envValues[field.key] ?? ''}
                onChange={e => setEnvValues(prev => ({ ...prev, [field.key]: e.target.value }))}
              />
            </div>
          ))}
          <div className="cv-form-actions">
            <Button variant="primary" size="sm" type="button" onClick={handleEnvSubmit}>
              Añadir
            </Button>
            <Button
              variant="ghost"
              size="sm"
              type="button"
              onClick={() => { setShowEnvForm(false); setEnvValues({}) }}
            >
              Cancelar
            </Button>
          </div>
        </div>
      </AnimatedExpanderContent>
    </motion.div>
  )
}

// ── Manual add form ───────────────────────────────────────────────────────────

interface AddMcpFormProps {
  onAdded: () => void
  onToast: (msg: string, kind: 'ok' | 'warn' | 'error') => void
}

function AddMcpForm({ onAdded, onToast }: AddMcpFormProps) {
  const [adding, setAdding] = useState(false)
  const labelRef = useRef<HTMLInputElement>(null)
  const argvRef = useRef<HTMLInputElement>(null)
  const envRef = useRef<HTMLTextAreaElement>(null)

  async function handleAdd() {
    const label = labelRef.current?.value.trim() ?? ''
    const argvRaw = argvRef.current?.value.trim() ?? ''
    if (!label || !argvRaw) { onToast('Nombre y comando de arranque son obligatorios', 'warn'); return }

    const argv = argvRaw.split(/\s+/).filter(Boolean)
    const envRaw = envRef.current?.value.trim() ?? ''
    const env: Record<string, string> = {}
    envRaw.split('\n').forEach(line => {
      const idx = line.indexOf('=')
      if (idx > 0) env[line.slice(0, idx).trim()] = line.slice(idx + 1).trim()
    })

    setAdding(true)
    try {
      const res = await addMcpServer({
        server_id: label.toLowerCase().replace(/\s+/g, '_'),
        label,
        argv,
        env,
      })
      const name = label
      if (res && res.tool_count === 0) {
        onToast(`"${name}" se conectó pero no tiene herramientas disponibles. Revisa su configuración.`, 'warn')
      } else {
        onToast('Herramienta añadida — tus agentes ya pueden usarla', 'ok')
      }
      if (labelRef.current) labelRef.current.value = ''
      if (argvRef.current) argvRef.current.value = ''
      if (envRef.current) envRef.current.value = ''
      onAdded()
    } catch (e) {
      onToast(e instanceof Error ? e.message : 'Error', 'error')
    } finally { setAdding(false) }
  }

  return (
    <motion.div className="cv-form-card" whileHover={{ y: -1 }} transition={TWEEN_FAST} layout>
      <h3 className="cv-form-title">Añadir herramienta externa</h3>
      <label className="cv-label" htmlFor="mcp-label">Nombre</label>
      <input
        id="mcp-label"
        ref={labelRef}
        className="cv-input"
        type="text"
        placeholder="Replicate, Brave…"
        autoComplete="off"
      />
      <label className="cv-label" htmlFor="mcp-argv">Comando de arranque</label>
      <input
        id="mcp-argv"
        ref={argvRef}
        className="cv-input"
        type="text"
        placeholder="npx -y @modelcontextprotocol/server-brave-search"
        autoComplete="off"
      />
      <label className="cv-label" htmlFor="mcp-env">Variables de configuración (CLAVE=VALOR, una por línea)</label>
      <textarea
        id="mcp-env"
        ref={envRef}
        className="cv-textarea"
        rows={3}
        placeholder="BRAVE_API_KEY=br-xxx"
      />
      <div className="cv-form-actions">
        <button
          className="cv-btn cv-btn--primary cv-btn--sm"
          onClick={handleAdd}
          disabled={adding}
        >
          {adding ? 'Añadiendo…' : 'Añadir'}
        </button>
      </div>
    </motion.div>
  )
}
