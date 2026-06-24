/**
 * ArchivosView — workspace file browser.
 *
 * Lists files the agent has written to the container workspace and provides
 * a download link for each one.  Empty state is shown when there are no files.
 */

import { useCallback, useEffect, useState } from 'react'
import { listWorkspaceFiles } from '../api/client'
import type { WorkspaceFile } from '../api/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function downloadUrl(name: string): string {
  return `/api/v1/workspace/file/${encodeURIComponent(name)}`
}

// ── State ─────────────────────────────────────────────────────────────────────

type ViewState =
  | { status: 'loading' }
  | { status: 'success'; files: WorkspaceFile[] }
  | { status: 'error'; message: string }

// ── File row ──────────────────────────────────────────────────────────────────

function FileRow({ file }: { file: WorkspaceFile }) {
  return (
    <li className="memory-item" style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-3)' }}>
      <FileIcon />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          className="memory-item__content"
          style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-label)' }}
        >
          {file.name}
        </div>
        <div className="memory-item__time">{formatBytes(file.size)}</div>
      </div>
      <a
        href={downloadUrl(file.name)}
        download={file.name}
        target="_blank"
        rel="noopener noreferrer"
        className="cv-btn cv-btn--ghost cv-btn--sm"
        aria-label={`Descargar ${file.name}`}
        style={{ flexShrink: 0 }}
      >
        Descargar
      </a>
    </li>
  )
}

function FileIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      style={{ flexShrink: 0, color: 'var(--ink4)' }}
    >
      <path
        d="M4 2h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path d="M9 2v3h3" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  )
}

// ── ArchivosView ──────────────────────────────────────────────────────────────

export default function ArchivosView() {
  const [view, setView] = useState<ViewState>({ status: 'loading' })

  const load = useCallback(async () => {
    setView({ status: 'loading' })
    try {
      const files = await listWorkspaceFiles()
      setView({ status: 'success', files: Array.isArray(files) ? files : [] })
    } catch (err) {
      setView({
        status: 'error',
        message: err instanceof Error ? err.message : 'No se pudieron cargar los archivos.',
      })
    }
  }, [])

  useEffect(() => { void load() }, [load])

  return (
    <>
      <header className="view-header">
        <h1 className="view-title">Archivos</h1>
        <p className="view-subtitle">Archivos creados por el agente en el espacio de trabajo.</p>
      </header>

      <div className="view-body cv-view-body">
        <section className="cv-section" aria-label="Archivos del agente">
          <div className="cv-section-head">
            <h2 className="cv-section-label">Archivos</h2>
            <div className="cv-section-head__right">
              <button
                type="button"
                className="cv-btn cv-btn--ghost cv-btn--sm"
                onClick={load}
                aria-label="Actualizar lista de archivos"
              >
                Actualizar
              </button>
            </div>
          </div>

          {view.status === 'loading' && (
            <div className="cv-skeleton" aria-busy="true" aria-label="Cargando archivos…" />
          )}

          {view.status === 'error' && (
            <p className="state-error" role="alert">{view.message}</p>
          )}

          {view.status === 'success' && view.files.length === 0 && (
            <div className="state-container">
              <svg width="32" height="32" viewBox="0 0 16 16" fill="none" aria-hidden="true" style={{ color: 'var(--ink4)', opacity: 0.5 }}>
                <path d="M4 2h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
                <path d="M9 2v3h3" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
              </svg>
              <p className="state-label">Aún no hay archivos. Lo que cree el agente aparecerá aquí.</p>
            </div>
          )}

          {view.status === 'success' && view.files.length > 0 && (
            <ul
              className="cv-list"
              role="list"
              aria-label={`${view.files.length} archivo${view.files.length === 1 ? '' : 's'}`}
            >
              {view.files.map(f => (
                <FileRow key={f.name} file={f} />
              ))}
            </ul>
          )}
        </section>
      </div>
    </>
  )
}
