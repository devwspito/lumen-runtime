/**
 * MemoriaView — agent long-term memory browser.
 *
 * - List: GET /memory (rows with truncated content)
 * - Full content: GET /memory/{entry_id}  (entry_id = "{target}:{entry_index}")
 * - Delete: DELETE /memory/{id}
 * - Search: GET /memory/search?q=
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { sileo } from 'sileo'
import { Brain, Search, Trash2 } from 'lucide-react'
import { listMemory, searchMemory, forgetMemoryItem, getMemoryEntry, ApiError } from '../api/client'
import type { MemoryItem, MemoryEntryDetail } from '../api/types'
import { Drawer } from '../components/ui/Drawer'
import { EmptyState } from '../components/ui/EmptyState'
import { PageHeader } from '../components/ui/PageHeader'
import { Button } from '../components/ui/Button'
import { Spinner } from '../components/ui/Spinner'

// ── State machine ─────────────────────────────────────────────────────────────

type MemoryState =
  | { status: 'loading' }
  | { status: 'success'; items: MemoryItem[]; query: string }
  | { status: 'error'; message: string }

type DrawerState =
  | { open: false }
  | { open: true; item: MemoryItem; detail: MemoryEntryDetail | null; loading: boolean }

// ── Helpers ───────────────────────────────────────────────────────────────────

function memoryContent(item: MemoryItem): string {
  return String(item.content_truncated ?? item.content ?? item.text ?? '').trim()
}

function formatDate(iso?: string): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString('es', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function entryId(item: MemoryItem): string {
  if (item.id) return item.id
  const target = item.target ?? ''
  const idx = item.entry_index ?? 0
  return target ? `${target}:${idx}` : ''
}

// ── Memory row ────────────────────────────────────────────────────────────────

interface MemoryRowProps {
  item: MemoryItem
  index: number
  onClick: () => void
}

function MemoryRow({ item, index, onClick }: MemoryRowProps) {
  const content = memoryContent(item)
  const time = formatDate(item.created_at)

  return (
    <li
      className="memory-item memory-item--clickable ds-list-item-enter"
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } }}
      aria-label={`Entrada de memoria ${index + 1}${item.target ? ` — ${item.target}` : ''}`}
      style={{ animationDelay: `${Math.min(index * 30, 300)}ms` }}
    >
      <div className="memory-item__content">{content}</div>
      {time && <div className="memory-item__time">{time}</div>}
    </li>
  )
}

// ── MemoriaView ───────────────────────────────────────────────────────────────

export default function MemoriaView() {
  const [state, setState] = useState<MemoryState>({ status: 'loading' })
  const [searchInput, setSearchInput] = useState('')
  const [drawer, setDrawer] = useState<DrawerState>({ open: false })
  const [deleting, setDeleting] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async (query = '') => {
    setState({ status: 'loading' })
    try {
      const raw = query ? await searchMemory(query) : await listMemory()
      const items = Array.isArray(raw) ? raw : []
      setState({ status: 'success', items, query })
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : 'No se pudo cargar la memoria.'
      setState({ status: 'error', message: msg })
      sileo.error({ title: msg })
    }
  }, [])

  useEffect(() => { void load() }, [load])

  function handleSearch() {
    void load(searchInput.trim())
  }

  function handleRetry() {
    setSearchInput('')
    void load('')
  }

  async function openDrawer(item: MemoryItem) {
    setDrawer({ open: true, item, detail: null, loading: true })
    const id = entryId(item)
    if (!id) {
      // No resolvable ID — show what we have without fetching
      setDrawer({ open: true, item, detail: null, loading: false })
      return
    }
    try {
      const detail = await getMemoryEntry(id)
      setDrawer(prev => prev.open ? { ...prev, detail, loading: false } : prev)
    } catch {
      // Show truncated content as fallback
      setDrawer(prev => prev.open ? { ...prev, detail: null, loading: false } : prev)
    }
  }

  function closeDrawer() {
    setDrawer({ open: false })
  }

  async function handleDelete() {
    if (!drawer.open) return
    const item = drawer.item
    const id = entryId(item)
    if (!id) { sileo.error({ title: 'No se puede eliminar esta entrada.' }); return }
    setDeleting(true)
    try {
      await forgetMemoryItem(id)
      sileo.success({ title: 'Entrada eliminada' })
      closeDrawer()
      void load(searchInput.trim())
    } catch (e) {
      sileo.error({ title: e instanceof Error ? e.message : 'Error al eliminar' })
    } finally {
      setDeleting(false)
    }
  }

  const isSuccess = state.status === 'success'
  const activeQuery = isSuccess ? state.query : ''

  return (
    <>
      <PageHeader
        title="Memoria"
        subtitle="Lo que Lumen recuerda entre conversaciones."
      />

      <div className="view-body cv-view-body">
        {/* Search */}
        <section className="cv-section" aria-label="Buscar en memoria">
          <div className="cv-search-row">
            <label className="sr-only" htmlFor="memory-search">Buscar en memoria</label>
            <div style={{ position: 'relative', flex: 1 }}>
              <Search
                size={14}
                aria-hidden="true"
                style={{
                  position: 'absolute', left: 10, top: '50%',
                  transform: 'translateY(-50%)', color: 'var(--ink4)',
                  pointerEvents: 'none',
                }}
              />
              <input
                id="memory-search"
                ref={inputRef}
                className="cv-input"
                type="search"
                placeholder="Buscar en memoria…"
                autoComplete="off"
                value={searchInput}
                onChange={e => setSearchInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') handleSearch() }}
                style={{ paddingLeft: 30 }}
              />
            </div>
            <Button
              variant="secondary"
              size="sm"
              onClick={handleSearch}
              loading={state.status === 'loading'}
            >
              Buscar
            </Button>
          </div>
        </section>

        {/* Results */}
        <section className="cv-section" aria-label="Entradas de memoria">
          <h2 className="cv-section-label">
            {activeQuery ? `Resultados para "${activeQuery}"` : 'Entradas recientes'}
          </h2>

          {state.status === 'loading' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }} aria-busy="true">
              {[...Array(4)].map((_, i) => (
                <div key={i} className="cv-skeleton" style={{ height: 52 }} />
              ))}
            </div>
          )}

          {state.status === 'error' && (
            <div role="alert">
              <p className="state-error">{state.message}</p>
              <Button variant="secondary" size="sm" onClick={handleRetry} style={{ marginTop: 8 }}>
                Reintentar
              </Button>
            </div>
          )}

          {isSuccess && state.items.length === 0 && (
            <EmptyState
              icon={<Brain size={40} />}
              title={activeQuery
                ? `Sin resultados para "${activeQuery}"`
                : 'Aún no hay recuerdos'}
              description={activeQuery
                ? undefined
                : 'Lumen irá guardando lo importante de tus conversaciones automáticamente.'}
            />
          )}

          {isSuccess && state.items.length > 0 && (
            <ul className="cv-list memory-list" role="list">
              {state.items.map((item, i) => (
                <MemoryRow
                  key={item.id ?? i}
                  item={item}
                  index={i}
                  onClick={() => void openDrawer(item)}
                />
              ))}
            </ul>
          )}
        </section>
      </div>

      {/* Full-content drawer */}
      <Drawer
        open={drawer.open}
        title={drawer.open && drawer.item.target ? drawer.item.target : 'Detalle'}
        onClose={closeDrawer}
      >
        {drawer.open && (
          <div className="mem-drawer-content">
            {drawer.item.target && (
              <p className="mem-drawer-target">{drawer.item.target}</p>
            )}

            {drawer.loading ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', padding: 'var(--sp-4) 0', color: 'var(--ink4)' }}>
                <Spinner size={16} label="Cargando contenido completo…" />
                <span style={{ fontSize: 'var(--text-label)' }}>Cargando…</span>
              </div>
            ) : (
              <p className="mem-drawer-body">
                {(drawer.detail?.content ?? memoryContent(drawer.item)) || '(sin contenido)'}
              </p>
            )}

            <div className="mem-drawer-actions">
              <Button
                variant="danger"
                size="sm"
                onClick={handleDelete}
                loading={deleting}
                aria-label="Eliminar esta entrada de memoria"
              >
                <Trash2 size={14} aria-hidden="true" />
                Eliminar
              </Button>
            </div>
          </div>
        )}
      </Drawer>
    </>
  )
}
