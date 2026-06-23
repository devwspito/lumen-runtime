/**
 * MemoriaView — agent long-term memory browser.
 * Mirrors vanilla memory.js: list all entries + search.
 * Endpoints: GET /memory, GET /memory/search?q=
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { sileo } from 'sileo'
import { listMemory, searchMemory, ApiError } from '../api/client'
import type { MemoryItem } from '../api/types'

// ── State machine ─────────────────────────────────────────────────────────────

type MemoryState =
  | { status: 'loading' }
  | { status: 'success'; items: MemoryItem[]; query: string }
  | { status: 'error'; message: string }

// ── Helpers ───────────────────────────────────────────────────────────────────

function memoryContent(item: MemoryItem): string {
  // Prefer the backend's pre-truncated field; fall back to full content or plain text.
  // Never JSON.stringify — that leaks raw object noise to the user.
  return String(item.content_truncated ?? item.content ?? item.text ?? '').trim()
}

function formatDate(iso?: string): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString('es')
}

// ── Memory item row ───────────────────────────────────────────────────────────

interface MemoryRowProps {
  item: MemoryItem
}

function MemoryRow({ item }: MemoryRowProps) {
  const content = memoryContent(item)
  const time = formatDate(item.created_at)

  return (
    <li className="memory-item">
      <div className="memory-item__content">{content}</div>
      {time && <div className="memory-item__time">{time}</div>}
    </li>
  )
}

// ── MemoriaView ───────────────────────────────────────────────────────────────

export default function MemoriaView() {
  const [state, setState] = useState<MemoryState>({ status: 'loading' })
  const [searchInput, setSearchInput] = useState('')
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

  useEffect(() => { load() }, [load])

  function handleSearch() {
    const q = searchInput.trim()
    load(q)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') handleSearch()
  }

  function handleRetry() {
    setSearchInput('')
    load('')
  }

  const isSuccess = state.status === 'success'
  const activeQuery = isSuccess ? state.query : ''

  return (
    <>
      <header className="view-header">
        <h1 className="view-title">Memoria</h1>
        <p className="view-subtitle">Lo que Lumen recuerda entre conversaciones.</p>
      </header>

      <div className="view-body cv-view-body">
        {/* Search */}
        <section className="cv-section" aria-label="Buscar en memoria">
          <div className="cv-search-row">
            <label className="sr-only" htmlFor="memory-search">
              Buscar en memoria
            </label>
            <input
              id="memory-search"
              ref={inputRef}
              className="cv-input"
              type="search"
              placeholder="Buscar en memoria…"
              autoComplete="off"
              aria-label="Buscar en memoria"
              value={searchInput}
              onChange={e => setSearchInput(e.target.value)}
              onKeyDown={handleKeyDown}
            />
            <button
              className="cv-btn cv-btn--secondary cv-btn--sm"
              onClick={handleSearch}
              type="button"
            >
              Buscar
            </button>
          </div>
        </section>

        {/* Results */}
        <section className="cv-section" aria-label="Entradas de memoria">
          <h2 className="cv-section-label">
            {activeQuery ? `Resultados para "${activeQuery}"` : 'Entradas recientes'}
          </h2>

          {state.status === 'loading' && (
            <div className="cv-skeleton" aria-busy="true" aria-label="Cargando memoria…" />
          )}

          {state.status === 'error' && (
            <div role="alert">
              <p className="state-error">{state.message}</p>
              <button
                className="cv-btn cv-btn--secondary cv-btn--sm"
                onClick={handleRetry}
                style={{ marginTop: 8 }}
                type="button"
              >
                Reintentar
              </button>
            </div>
          )}

          {isSuccess && state.items.length === 0 && (
            <p className="cv-empty">
              {activeQuery
                ? `Sin resultados para "${activeQuery}"`
                : 'Aún no hay recuerdos. Lumen irá guardando lo importante de tus conversaciones automáticamente.'}
            </p>
          )}

          {isSuccess && state.items.length > 0 && (
            <ul className="cv-list memory-list" role="list">
              {state.items.map((item, i) => (
                <MemoryRow key={item.id ?? i} item={item} />
              ))}
            </ul>
          )}
        </section>
      </div>
    </>
  )
}
