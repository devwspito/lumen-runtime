import { useEffect, useReducer, useRef, useState } from 'react'
import { listMcpServers, addMcpServer, removeMcpServer, searchMcpRegistry, ApiError } from '../api/client'
import type { McpServer, McpRegistryEntry } from '../api/types'

// Curated catalog — mirrors mcp.js MCP_CATALOG (npx-only verified servers).
const MCP_CATALOG: McpRegistryEntry[] = [
  {
    server_id: 'github',
    label: 'GitHub',
    tag: 'Dev',
    description: 'MCP oficial de GitHub: repos, issues, PRs, código.',
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
    label: 'Filesystem',
    tag: 'Sistema',
    description: 'Lectura/escritura de ficheros locales con HITL.',
    argv: ['npx', '-y', '@modelcontextprotocol/server-filesystem', '/var/lib/hermes/workspace'],
    repository: 'https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem',
  },
]

function slugify(name: string): string {
  return String(name || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'mcp-server'
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

// Mirrors mcp.js BYOK env collection using the browser prompt (same as vanilla promptDialog).
async function collectEnv(
  entry: McpRegistryEntry,
): Promise<Record<string, string> | null> {
  const rawVars = entry.env_vars ?? []
  const schema = rawVars.map(v =>
    typeof v === 'string'
      ? { key: v, label: v, required: false, secret: false }
      : v,
  )
  const env: Record<string, string> = {}
  for (const field of schema) {
    const label = `${field.label ?? field.key}${field.required ? ' *' : ''}`
    const val = window.prompt(label)
    if (val === null) { if (field.required) return null; continue }
    if (val.trim()) env[field.key] = val.trim()
    else if (field.required) return null
  }
  return env
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

interface Toast { id: number; message: string; kind: 'ok' | 'warn' | 'error'; durationMs?: number }
let seq = 0
function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const show = (message: string, kind: Toast['kind'] = 'ok', durationMs = 4000) => {
    const id = ++seq
    setToasts(t => [...t, { id, message, kind, durationMs }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), durationMs)
  }
  return { toasts, show }
}

export default function McpView() {
  const [state, dispatch] = useReducer(reducer, { status: 'loading' })
  const [registryResults, setRegistryResults] = useState<McpRegistryEntry[]>([])
  const [registryLoading, setRegistryLoading] = useState(false)
  const { toasts, show } = useToasts()
  const regInputRef = useRef<HTMLInputElement>(null)

  function load() {
    dispatch({ type: 'LOADING' })
    listMcpServers()
      .then(servers => dispatch({ type: 'LOADED', servers }))
      .catch((e: unknown) => dispatch({
        type: 'FAILED',
        message: e instanceof ApiError ? e.message : 'No se pudieron cargar los servidores MCP.',
      }))
  }

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const installedIds = state.status === 'success'
    ? new Set(state.servers.map(s => s.server_id ?? s.id ?? ''))
    : new Set<string>()

  async function installEntry(entry: McpRegistryEntry, onDone: () => void) {
    const runner = getRunner(entry.argv)
    if (runner && runner !== 'npx') {
      show(`Solo se admiten servidores npx por ahora (este usa ${runner}).`, 'warn', 7000)
      return
    }
    const collectedEnv = await collectEnv(entry)
    if (collectedEnv === null) return

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
        show(`"${name}" se conectó pero no expone herramientas. Revisa su configuración.`, 'warn', 7000)
      } else {
        show(`Servidor "${name}" añadido`, 'ok')
      }
      load()
      onDone()
    } catch (e) {
      show(e instanceof Error ? e.message : 'Error', 'error')
      onDone()
    }
  }

  async function searchRegistry() {
    const q = regInputRef.current?.value.trim() ?? ''
    if (q.length < 2) return
    setRegistryLoading(true)
    try {
      const results = await searchMcpRegistry(q)
      const arr = Array.isArray(results) ? results : []
      setRegistryResults(arr)
    } finally {
      setRegistryLoading(false)
    }
  }

  return (
    <>
      <header className="view-header">
        <h1 className="view-title">Servidores MCP</h1>
        <p className="view-subtitle">Model Context Protocol. Conecta servidores de herramientas externos.</p>
      </header>

      <div className="view-body cv-view-body">
        <ToastList toasts={toasts} />

        {/* ── Active servers ──────────────────────────────────────────────── */}
        <section className="cv-section" aria-label="Servidores activos">
          <h2 className="cv-section-label">Servidores activos</h2>
          {state.status === 'loading' && <div className="cv-skeleton" aria-busy="true" />}
          {state.status === 'error' && (
            <div role="alert">
              <p className="state-error">{state.message}</p>
              <button className="cv-btn cv-btn--secondary cv-btn--sm" onClick={load} style={{ marginTop: 8 }}>Reintentar</button>
            </div>
          )}
          {state.status === 'success' && (
            state.servers.length === 0
              ? <p className="cv-empty">Sin servidores MCP. Añade uno.</p>
              : (
                <ul className="cv-list" role="list">
                  {state.servers.map(s => (
                    <li key={s.server_id ?? s.id}>
                      <McpServerRow
                        server={s}
                        onRemove={async () => {
                          const name = s.label ?? s.server_id ?? ''
                          if (!window.confirm(`¿Eliminar "${name}"?`)) return
                          try {
                            await removeMcpServer(s.server_id ?? s.id ?? '')
                            show('Servidor eliminado', 'ok')
                            load()
                          } catch (e) {
                            show(e instanceof Error ? e.message : 'Error', 'error')
                          }
                        }}
                      />
                    </li>
                  ))}
                </ul>
              )
          )}
        </section>

        {/* ── Suggested catalog ───────────────────────────────────────────── */}
        <section className="cv-section" aria-label="Servidores sugeridos">
          <h2 className="cv-section-label">Sugeridos</h2>
          <div className="mcp-cards-grid">
            {MCP_CATALOG.map(entry => (
              <CatalogCard
                key={entry.server_id}
                entry={entry}
                installedIds={installedIds}
                onInstall={installEntry}
              />
            ))}
          </div>
        </section>

        {/* ── Official registry search ─────────────────────────────────── */}
        <section className="cv-section" aria-label="Registro oficial MCP">
          <h2 className="cv-section-label">Registro oficial MCP</h2>
          <div className="cv-search-row">
            <label className="sr-only" htmlFor="mcp-registry-input">Buscar en el registro oficial</label>
            <input
              id="mcp-registry-input"
              ref={regInputRef}
              className="cv-input"
              type="search"
              placeholder="Buscar en el registro oficial (github, slack, postgres…)"
              autoComplete="off"
              onKeyDown={e => { if (e.key === 'Enter') searchRegistry() }}
            />
            <button
              className="cv-btn cv-btn--secondary cv-btn--sm"
              onClick={searchRegistry}
              disabled={registryLoading}
            >
              {registryLoading ? 'Buscando…' : 'Buscar'}
            </button>
          </div>
          <p className="cv-hint">Conectado a registry.modelcontextprotocol.io</p>
          {registryResults.length > 0 && (
            <div className="mcp-cards-grid">
              {registryResults.map((entry, i) => (
                <CatalogCard
                  key={`${entry.server_id ?? entry.id ?? entry.name ?? i}`}
                  entry={entry}
                  installedIds={installedIds}
                  onInstall={installEntry}
                />
              ))}
            </div>
          )}
          {!registryLoading && registryResults.length === 0 && regInputRef.current?.value && (
            <p className="cv-empty">Sin resultados en el registro.</p>
          )}
        </section>

        {/* ── Manual add ──────────────────────────────────────────────────── */}
        <section className="cv-section" aria-label="Añadir manualmente">
          <h2 className="cv-section-label">Añadir manualmente</h2>
          <AddMcpForm onAdded={() => { show('Servidor MCP añadido', 'ok'); load() }} onToast={show} />
        </section>
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
  const argv = Array.isArray(server.argv) ? server.argv.join(' ') : (server.argv ?? '')
  const healthy = String(server.health ?? '').toLowerCase() === 'healthy'
  const hasHealth = server.health != null && server.health !== ''
  const tools = server.tool_count != null ? `${server.tool_count} tools` : ''

  return (
    <div className="mcp-row">
      <div className="mcp-row__info">
        <div className="mcp-row__name">
          {server.label ?? server.server_id ?? 'MCP Server'}
          {hasHealth && (
            <span className={`mcp-health-chip${healthy ? ' is-ok' : ' is-down'}`}>
              {healthy ? '●' : '○'} {tools || String(server.health)}
            </span>
          )}
          {!hasHealth && tools && <span className="mcp-health-chip">{tools}</span>}
        </div>
        {argv && <div className="mcp-row__cmd">{argv}</div>}
      </div>
      <button
        className="cv-btn cv-btn--ghost cv-btn--sm cv-btn--danger"
        onClick={onRemove}
        aria-label={`Eliminar servidor MCP ${server.label ?? ''}`}
      >
        ✕
      </button>
    </div>
  )
}

// ── Catalog / registry card ───────────────────────────────────────────────────

interface CatalogCardProps {
  entry: McpRegistryEntry
  installedIds: Set<string>
  onInstall: (entry: McpRegistryEntry, onDone: () => void) => void
}

function CatalogCard({ entry, installedIds, onInstall }: CatalogCardProps) {
  const [installing, setInstalling] = useState(false)
  const id = entry.server_id ?? entry.id ?? slugify(entry.name ?? '')
  const already = installedIds.has(id) || installedIds.has(entry.server_id ?? '')
  const runner = getRunner(entry.argv)
  const nonNpx = runner !== '' && runner !== 'npx'
  const unsupported = entry.installable === false || nonNpx
  const argv = Array.isArray(entry.argv) ? entry.argv.join(' ') : (entry.argv ?? '')
  const needsEnv = Array.isArray(entry.env_vars) && entry.env_vars.length > 0
  const repo = entry.repository ?? entry.homepage ?? entry.website ?? ''

  function handleInstall() {
    setInstalling(true)
    onInstall(entry, () => setInstalling(false))
  }

  return (
    <div className="mcp-card">
      <div className="mcp-card__info">
        <div className="mcp-card__head">
          <span className="mcp-card__name">{entry.label ?? entry.name ?? id}</span>
          {entry.tag && <span className="mcp-card__tag">{entry.tag}</span>}
          {needsEnv && <span className="mcp-card__tag">BYOK</span>}
        </div>
        {entry.description && <div className="mcp-card__desc">{entry.description}</div>}
        {argv && <div className="mcp-card__cmd">{argv}</div>}
        {unsupported && entry.unsupported_reason && (
          <div className="mcp-card__cmd">{entry.unsupported_reason}</div>
        )}
        {unsupported && nonNpx && !entry.unsupported_reason && (
          <div className="mcp-card__cmd">Solo se admiten servidores npx por ahora (este usa {runner}).</div>
        )}
      </div>
      <div className="mcp-card__actions">
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
        <button
          className="cv-btn cv-btn--secondary cv-btn--sm"
          disabled={already || unsupported || installing}
          onClick={handleInstall}
        >
          {already ? 'Añadido' : unsupported ? 'No disponible' : installing ? 'Añadiendo…' : 'Añadir'}
        </button>
      </div>
    </div>
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
    if (!label || !argvRaw) { onToast('Nombre y comando son obligatorios', 'warn'); return }

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
        onToast(`"${name}" se conectó pero no expone herramientas. Revisa su configuración.`, 'warn')
      } else {
        onToast('Servidor MCP añadido', 'ok')
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
    <div className="cv-form-card">
      <h3 className="cv-form-title">Añadir servidor MCP</h3>
      <label className="cv-label" htmlFor="mcp-label">Nombre</label>
      <input
        id="mcp-label"
        ref={labelRef}
        className="cv-input"
        type="text"
        placeholder="Replicate, Brave…"
        autoComplete="off"
      />
      <label className="cv-label" htmlFor="mcp-argv">Comando (argv separado por espacios)</label>
      <input
        id="mcp-argv"
        ref={argvRef}
        className="cv-input"
        type="text"
        placeholder="npx -y @modelcontextprotocol/server-brave-search"
        autoComplete="off"
      />
      <label className="cv-label" htmlFor="mcp-env">Variables de entorno (KEY=VALUE, una por línea)</label>
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
    </div>
  )
}

// ── Toast list ────────────────────────────────────────────────────────────────

function ToastList({ toasts }: { toasts: Toast[] }) {
  if (toasts.length === 0) return null
  return (
    <div className="cv-toast-list" aria-live="polite" aria-atomic="false">
      {toasts.map(t => (
        <div key={t.id} className={`cv-toast cv-toast--${t.kind}`} role="status">
          {t.message}
        </div>
      ))}
    </div>
  )
}
