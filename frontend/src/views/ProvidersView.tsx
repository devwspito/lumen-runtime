import { useEffect, useReducer, useRef, useState } from 'react'
import { sileo } from 'sileo'
import { X, Cpu, Cloud, Server, Globe } from 'lucide-react'
import {
  listProviders, listNativeProviders, addProvider, setActiveProvider,
  testProvider, deleteProvider, startProviderOAuth, getProviderOAuthStatus,
  ApiError,
} from '../api/client'
import type { Provider } from '../api/types'
import { useConfirmDialog } from '../components/ConfirmDialog'
import Badge from '../components/Badge'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { Button } from '../components/ui/Button'
import {
  AnimatePresence,
  AnimatedListItem,
  AnimatedExpanderContent,
  FadeIn,
  Stagger,
  StaggerItem,
  motion,
  useReducedMotion,
  SPRING,
  TWEEN_FAST,
} from '../components/ui/motion'

// Mirrors vanilla providers.js: badge colours per kind/auth-type
const KIND_COLORS: Record<string, string> = {
  anthropic: '#D97706', openai: '#10A37F', openai_compatible: '#10A37F',
  google: '#4285F4', gemini: '#4285F4', azure: '#0078D4', mistral: '#FF7000',
  groq: '#F55036', ollama: '#6B7280', nous: '#7C3AED', cohere: '#39594D',
  vllm: '#7C3AED', oauth: '#8B5CF6', 'api key': '#6B7280', subscription: '#8B5CF6',
  modelo: '#6B7280',
}

const OAUTH_IDS = new Set(['nous', 'openai-codex', 'xai-oauth'])

function badgeLabel(p: Provider): string {
  if (p.kind) return p.kind
  const a = String(p.auth_type ?? '').toLowerCase()
  if (a.includes('oauth')) return 'OAuth'
  if (a.includes('api')) return 'API key'
  return 'Modelo'
}

function isOAuthProvider(p: Provider): boolean {
  const id = p.provider_id ?? ''
  return Boolean(p.supports_oauth)
    || /oauth/i.test(String(p.auth_type ?? ''))
    || OAUTH_IDS.has(id)
}

function providerName(p: Provider): string {
  return p.alias ?? p.name ?? p.provider_id ?? ''
}

function ProviderChip({ provider }: { provider: Provider }) {
  const kind = String(provider.kind ?? provider.category ?? '').toLowerCase()
  const isLocal = kind === 'ollama' || kind === 'vllm' || kind === 'openai_compatible'
  const isCloud = kind === 'anthropic' || kind === 'openai' || kind === 'google' ||
    kind === 'gemini' || kind === 'azure' || kind === 'mistral' || kind === 'groq'
  const IconEl = isLocal ? Server : isCloud ? Cloud : Globe
  return (
    <span className="ds-icon-chip ds-icon-chip--neutral" aria-hidden="true">
      <IconEl size={14} />
    </span>
  )
}

// Discriminated state to make impossible combinations unreachable
type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'success'; configured: Provider[]; native: Provider[] }

type Action =
  | { type: 'LOADED'; configured: Provider[]; native: Provider[] }
  | { type: 'FAILED'; message: string }
  | { type: 'RELOAD' }

function reducer(_state: State, action: Action): State {
  switch (action.type) {
    case 'LOADED': return { status: 'success', configured: action.configured, native: action.native }
    case 'FAILED': return { status: 'error', message: action.message }
    case 'RELOAD': return { status: 'loading' }
  }
}

function show(message: string, kind: 'ok' | 'warn' | 'error' = 'ok') {
  if (kind === 'ok') sileo.success({ title: message })
  else if (kind === 'error') sileo.error({ title: message })
  else sileo.warning({ title: message })
}

// Skeleton loading rows with shimmer
function SkeletonRows({ count }: { count: number }) {
  return (
    <Stagger style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {[...Array(count)].map((_, i) => (
        <StaggerItem key={i}>
          <div className="cv-skeleton" style={{ height: 52 }} />
        </StaggerItem>
      ))}
    </Stagger>
  )
}

export default function ProvidersView() {
  const [state, dispatch] = useReducer(reducer, { status: 'loading' })
  const [confirm, ConfirmDialogNode] = useConfirmDialog()

  function load() {
    dispatch({ type: 'RELOAD' })
    Promise.all([listProviders(), listNativeProviders()])
      .then(([configured, native]) => {
        dispatch({
          type: 'LOADED',
          configured: Array.isArray(configured) ? configured : [],
          native: Array.isArray(native) ? native : [],
        })
      })
      .catch((err: unknown) => {
        dispatch({
          type: 'FAILED',
          message: err instanceof ApiError ? err.message : 'No se pudieron cargar los proveedores.',
        })
      })
  }

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const configuredIds = state.status === 'success'
    ? new Set(state.configured.map(p => p.provider_id))
    : new Set<string>()

  return (
    <>
      {ConfirmDialogNode}
      <PageHeader
        title="Proveedores"
        subtitle="Conecta modelos de IA. Activa el que Lumen usará por defecto."
      />

      <div className="view-body cv-view-body">
        {state.status === 'loading' && (
          <SkeletonRows count={3} />
        )}

        {state.status === 'error' && (
          <FadeIn>
            <div className="state-container" role="alert">
              <p className="state-error">{state.message}</p>
              <Button variant="secondary" onClick={load}>Reintentar</Button>
            </div>
          </FadeIn>
        )}

        {state.status === 'success' && (
          <Stagger style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-8)' }}>
            <StaggerItem>
              <section className="cv-section" aria-label="Proveedores configurados">
                <h2 className="cv-section-label">Configurados</h2>
                {state.configured.length === 0
                  ? (
                    <EmptyState
                      icon={<Cpu size={36} />}
                      title="Sin proveedores configurados"
                      description="Añade uno del catálogo para empezar a chatear."
                    />
                  )
                  : (
                    <ul className="cv-list" role="list">
                      <AnimatePresence initial={false}>
                        {state.configured.map(p => (
                          <AnimatedListItem key={p.provider_id}>
                            <ProviderRow
                              provider={p}
                              isConfigured
                              onRefresh={load}
                              onToast={show}
                              onConfirm={confirm}
                            />
                          </AnimatedListItem>
                        ))}
                      </AnimatePresence>
                    </ul>
                  )
                }
              </section>
            </StaggerItem>

            <StaggerItem>
              <section className="cv-section" aria-label="Modelo propio o local">
                <h2 className="cv-section-label">Modelo propio / local</h2>
                <CustomProviderCard onAdded={load} onToast={show} />
              </section>
            </StaggerItem>

            <StaggerItem>
              <section className="cv-section" aria-label="Catálogo nativo Hermes">
                <h2 className="cv-section-label">Catálogo nativo Hermes</h2>
                {state.native.length === 0
                  ? <p className="cv-empty">Catálogo no disponible en esta versión.</p>
                  : (
                    <ul className="cv-list" role="list">
                      <AnimatePresence initial={false}>
                        {state.native
                          .filter(p => !configuredIds.has(p.provider_id))
                          .map(p => (
                            <AnimatedListItem key={p.provider_id}>
                              <ProviderRow
                                provider={p}
                                isConfigured={false}
                                onRefresh={load}
                                onToast={show}
                                onConfirm={confirm}
                              />
                            </AnimatedListItem>
                          ))
                        }
                      </AnimatePresence>
                    </ul>
                  )
                }
              </section>
            </StaggerItem>
          </Stagger>
        )}
      </div>
    </>
  )
}

// ── Provider row ──────────────────────────────────────────────────────────────

type ConfirmFn = (opts: import('../components/ConfirmDialog').ConfirmOptions) => Promise<boolean>

interface ProviderRowProps {
  provider: Provider
  isConfigured: boolean
  onRefresh: () => void
  onToast: (msg: string, kind: 'ok' | 'warn' | 'error') => void
  onConfirm: ConfirmFn
}

function ProviderRow({ provider, isConfigured, onRefresh, onToast, onConfirm }: ProviderRowProps) {
  const reduced = useReducedMotion()
  const [testing, setTesting] = useState(false)
  const [oauthPending, setOauthPending] = useState(false)
  const [showKeyForm, setShowKeyForm] = useState(false)
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [addingKey, setAddingKey] = useState(false)
  const [addConnFailed, setAddConnFailed] = useState(false)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const label = badgeLabel(provider)
  const color = KIND_COLORS[label.toLowerCase()] ?? '#6B7280'
  const name = providerName(provider)
  const id = provider.provider_id ?? ''

  async function handleActivate() {
    try {
      await setActiveProvider(id)
      onToast(`${name} activado — ya puedes chatear`, 'ok')
      onRefresh()
    } catch (e) {
      onToast(e instanceof Error ? e.message : 'Error', 'error')
    }
  }

  async function handleTest() {
    setTesting(true)
    try {
      const r = await testProvider(id)
      onToast(r?.ok ? 'Conexión exitosa' : 'Sin respuesta del servidor', r?.ok ? 'ok' : 'warn')
    } catch (e) {
      onToast(e instanceof Error ? e.message : 'Error', 'error')
    } finally { setTesting(false) }
  }

  async function handleDelete() {
    const isActive = isConfigured && provider.is_active
    const ok = await onConfirm({
      title: `¿Eliminar ${name}?`,
      description: isActive
        ? 'Este es el proveedor activo. Si lo eliminas, el chat dejará de funcionar hasta que configures otro.'
        : undefined,
      confirmLabel: 'Eliminar',
      variant: 'danger',
    })
    if (!ok) return
    try {
      await deleteProvider(id)
      onToast('Proveedor eliminado', 'ok')
      onRefresh()
    } catch (e) {
      onToast(e instanceof Error ? e.message : 'Error', 'error')
    }
  }

  async function handleAddConfirm() {
    if (!apiKeyInput.trim()) { onToast('Introduce la clave API', 'warn'); return }
    setAddingKey(true)
    try {
      await addProvider({
        provider_id: id,
        alias: provider.alias ?? provider.name,
        api_key: apiKeyInput.trim(),
        kind: provider.kind ?? provider.category,
      })
      setShowKeyForm(false)
      setApiKeyInput('')

      let testPassed = false
      try {
        const r = await testProvider(id)
        testPassed = r?.ok === true
      } catch {
        testPassed = false
      }

      if (testPassed) {
        await setActiveProvider(id)
        setAddConnFailed(false)
        onToast(`${name} conectado y verificado — pruébalo en el chat`, 'ok')
        onRefresh()
      } else {
        setAddConnFailed(true)
        onRefresh()
      }
    } catch (e) {
      onToast(e instanceof Error ? e.message : 'Error', 'error')
    } finally {
      setAddingKey(false)
    }
  }

  async function handleOAuth() {
    setOauthPending(true)
    let r: Record<string, unknown>
    try {
      r = await startProviderOAuth(id)
    } catch (e) {
      onToast(e instanceof Error ? e.message : 'No se pudo conectar', 'error')
      setOauthPending(false)
      return
    }

    if (!r || r['error']) {
      onToast(`No se pudo conectar: ${(r?.['error'] as string) ?? 'error desconocido'}`, 'error')
      setOauthPending(false)
      return
    }

    const session = r['session_id'] as string | undefined
    const url = (r['auth_url'] ?? r['verification_url']) as string | undefined
    const code = r['user_code'] as string | undefined

    if (url) {
      window.open(url, '_blank', 'noopener,noreferrer')
      onToast(`Abriendo el navegador para conectar ${name}…`, 'ok')
    }
    if (code) {
      onToast(`Ve a ${url ?? ''} e introduce el código: ${code}`, 'ok')
    } else {
      onToast(`Esperando autorización de ${name}…`, 'ok')
    }

    if (!session) { setOauthPending(false); return }

    const intervalMs = Math.max(2000, ((r['poll_interval'] as number | undefined) ?? 4) * 1000)
    const deadline = Date.now() + Math.max(60, ((r['expires_in'] as number | undefined) ?? 600)) * 1000

    const poll = async () => {
      if (Date.now() > deadline) {
        onToast('La sesión de conexión expiró — vuelve a intentarlo', 'warn')
        setOauthPending(false)
        return
      }
      const st = await getProviderOAuthStatus(session)
      const status = String(st?.status ?? '').toLowerCase()
      if (status === 'approved' || status === 'connected' || status === 'success') {
        onToast(`${name} conectado — pruébalo en el chat`, 'ok')
        setOauthPending(false)
        onRefresh()
        return
      }
      if (status === 'error' || status === 'failed') {
        onToast(`No se pudo conectar: ${st?.error_message ?? st?.error ?? 'error desconocido'}`, 'error')
        setOauthPending(false)
        return
      }
      if (status === 'expired') {
        onToast('La sesión de conexión expiró — vuelve a intentarlo', 'warn')
        setOauthPending(false)
        return
      }
      pollRef.current = setTimeout(poll, intervalMs)
    }
    pollRef.current = setTimeout(poll, intervalMs)
  }

  useEffect(() => () => {
    if (pollRef.current) clearTimeout(pollRef.current)
  }, [])

  return (
    <motion.div
      className={`provider-row${isConfigured && provider.is_active ? ' provider-row--active' : ''}`}
      whileHover={reduced ? undefined : { y: -2 }}
      transition={SPRING}
      layout
    >
      <ProviderChip provider={provider} />
      <div className="provider-row__left">
        <div className="provider-row__name">{name}</div>
        <div className="provider-row__meta">
          <span
            className="provider-badge"
            style={{ background: `${color}22`, color }}
          >
            {label}
          </span>
          {provider.default_model && (
            <span className="provider-row__model">{provider.default_model}</span>
          )}
          {isConfigured && provider.is_active && (
            <Badge variant="ok">Activo</Badge>
          )}
          {addConnFailed && (
            <Badge variant="danger"><span role="alert">Conexión fallida — revisa la clave</span></Badge>
          )}
        </div>
      </div>
      <div className="provider-row__actions">
        {isConfigured ? (
          <>
            {!provider.is_active && (
              <button className="cv-btn cv-btn--secondary cv-btn--sm" onClick={handleActivate}>
                Activar
              </button>
            )}
            <button
              className="cv-btn cv-btn--ghost cv-btn--sm"
              onClick={handleTest}
              disabled={testing}
            >
              {testing ? 'Probando…' : 'Probar'}
            </button>
            <button
              className="cv-btn cv-btn--ghost cv-btn--sm cv-btn--danger"
              onClick={handleDelete}
              aria-label={`Eliminar proveedor ${name}`}
            >
              <X size={14} aria-hidden="true" />
            </button>
          </>
        ) : isOAuthProvider(provider) ? (
          <button
            className="cv-btn cv-btn--secondary cv-btn--sm"
            onClick={handleOAuth}
            disabled={oauthPending}
          >
            {oauthPending ? 'Conectando…' : 'Conectar'}
          </button>
        ) : !showKeyForm ? (
          <button
            className="cv-btn cv-btn--secondary cv-btn--sm"
            onClick={() => { setShowKeyForm(true); setAddConnFailed(false) }}
          >
            {addConnFailed ? 'Reintentar clave' : 'Añadir'}
          </button>
        ) : null}
      </div>

      {/* Animated inline API-key form */}
      <AnimatedExpanderContent open={!isConfigured && !isOAuthProvider(provider) && showKeyForm}>
        <div className="cv-form-inline" style={{ marginTop: 'var(--sp-3)', flexWrap: 'wrap', gap: 'var(--sp-2)' }}>
          <label className="sr-only" htmlFor={`pv-key-${id}`}>Clave API para {name}</label>
          <input
            id={`pv-key-${id}`}
            className="cv-input"
            type="password"
            autoComplete="new-password"
            placeholder={`Clave API para ${name}`}
            value={apiKeyInput}
            onChange={e => setApiKeyInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') void handleAddConfirm() }}
            style={{ flex: 1, minWidth: 180 }}
          />
          <motion.button
            className="cv-btn cv-btn--primary cv-btn--sm"
            onClick={handleAddConfirm}
            disabled={addingKey}
            type="button"
            whileTap={reduced ? undefined : { scale: 0.97 }}
            transition={TWEEN_FAST}
          >
            {addingKey ? 'Guardando…' : 'Guardar'}
          </motion.button>
          <button
            className="cv-btn cv-btn--ghost cv-btn--sm"
            onClick={() => { setShowKeyForm(false); setApiKeyInput('') }}
            type="button"
          >
            Cancelar
          </button>
        </div>
      </AnimatedExpanderContent>
    </motion.div>
  )
}

// ── Custom provider card (OpenAI-compatible) ──────────────────────────────────

interface CustomProviderCardProps {
  onAdded: () => void
  onToast: (msg: string, kind: 'ok' | 'warn' | 'error') => void
}

function CustomProviderCard({ onAdded, onToast }: CustomProviderCardProps) {
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [connFailed, setConnFailed] = useState(false)
  const reduced = useReducedMotion()
  const aliasRef = useRef<HTMLInputElement>(null)
  const urlRef = useRef<HTMLInputElement>(null)
  const modelRef = useRef<HTMLInputElement>(null)
  const keyRef = useRef<HTMLInputElement>(null)

  async function handleSave() {
    const base_url = urlRef.current?.value.trim() ?? ''
    const default_model = modelRef.current?.value.trim() ?? ''
    const alias = aliasRef.current?.value.trim() || default_model || 'Modelo local'
    const api_key = keyRef.current?.value.trim() || undefined

    if (!base_url || !default_model) {
      onToast('Pon al menos la URL base y el nombre del modelo.', 'warn')
      return
    }

    setSaving(true)
    try {
      const added = await addProvider({ kind: 'openai_compatible', alias, default_model, base_url, api_key })
      const newId = (added as { provider_id?: string }).provider_id ?? alias

      let testPassed = false
      try {
        const r = await testProvider(newId)
        testPassed = r?.ok === true
      } catch {
        testPassed = false
      }

      if (testPassed) {
        await setActiveProvider(newId)
        setConnFailed(false)
        setOpen(false)
        if (aliasRef.current) aliasRef.current.value = ''
        if (urlRef.current) urlRef.current.value = ''
        if (modelRef.current) modelRef.current.value = ''
        if (keyRef.current) keyRef.current.value = ''
        onToast('Modelo propio añadido y activado — pruébalo en el chat', 'ok')
        onAdded()
      } else {
        setConnFailed(true)
        onAdded()
      }
    } catch (e) {
      onToast(e instanceof Error ? e.message : 'Error', 'error')
    } finally { setSaving(false) }
  }

  return (
    <motion.div className="cv-teach-card" layout>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--sp-3)' }}>
        <p className="cv-teach-intro" style={{ margin: 0 }}>
          Conecta cualquier servidor compatible: vLLM, LM Studio, Ollama o uno propio.
        </p>
        {!open && (
          <Button variant="secondary" size="sm" onClick={() => setOpen(true)} style={{ alignSelf: 'flex-start', flexShrink: 0 }}>
            Añadir modelo propio
          </Button>
        )}
      </div>

      <AnimatedExpanderContent open={open}>
        <div className="cv-form-stack" style={{ paddingTop: 'var(--sp-3)' }}>
          <label className="cv-label" htmlFor="pv-c-alias">Nombre</label>
          <input
            id="pv-c-alias"
            ref={aliasRef}
            className="cv-input"
            type="text"
            placeholder='Nombre (p. ej. "Qwen local")'
            autoComplete="off"
          />
          <label className="cv-label" htmlFor="pv-c-url">URL base del servidor</label>
          <input
            id="pv-c-url"
            ref={urlRef}
            className="cv-input"
            type="text"
            placeholder="URL base (p. ej. https://tu-servidor/v1)"
            autoComplete="off"
          />
          <label className="cv-label" htmlFor="pv-c-model">Modelo</label>
          <input
            id="pv-c-model"
            ref={modelRef}
            className="cv-input"
            type="text"
            placeholder="Modelo (p. ej. qwen3.6-35b-a3b)"
            autoComplete="off"
          />
          <label className="cv-label" htmlFor="pv-c-key">Clave API</label>
          <input
            id="pv-c-key"
            ref={keyRef}
            className="cv-input"
            type="password"
            placeholder="Clave API (si tu servidor la requiere)"
            autoComplete="new-password"
          />
          <p className="cv-hint">La URL base debe terminar en /v1. La clave API solo si tu servidor la pide.</p>
          {connFailed && (
            <motion.p
              className="office-field-error"
              role="alert"
              style={{ color: '#EF4444', fontWeight: 500 }}
              initial={reduced ? false : { opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
            >
              Conexión fallida — el modelo se guardó pero no está activo. Corrige la URL o la clave y vuelve a guardar.
            </motion.p>
          )}
          <div className="cv-form-actions">
            <button
              className="cv-btn cv-btn--primary cv-btn--sm"
              onClick={handleSave}
              disabled={saving}
            >
              {saving ? 'Guardando…' : (connFailed ? 'Reintentar conexión' : 'Guardar y activar')}
            </button>
            <button
              className="cv-btn cv-btn--ghost cv-btn--sm"
              onClick={() => setOpen(false)}
            >
              Cancelar
            </button>
          </div>
        </div>
      </AnimatedExpanderContent>
    </motion.div>
  )
}
