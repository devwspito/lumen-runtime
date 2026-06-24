/**
 * ArchivosView — macOS Finder-style workspace file browser.
 *
 * API:
 *   GET /api/v1/workspace/files?path=<relpath>  →  WorkspaceFile[]
 *   GET /api/v1/workspace/download?path=<relpath>  →  binary download
 */

import { useCallback, useEffect, useState } from 'react'
import {
  Folder, FileText, FileCode, FileImage, File,
  LayoutGrid, List, RefreshCw, Download, ChevronRight,
  Loader2,
} from 'lucide-react'
import { listWorkspaceFiles, workspaceDownloadUrl } from '../api/client'
import type { WorkspaceFile } from '../api/types'
import { Drawer } from '../components/ui/Drawer'
import { EmptyState } from '../components/ui/EmptyState'
import { PageHeader } from '../components/ui/PageHeader'
import { Button } from '../components/ui/Button'

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatBytes(bytes: number | undefined): string {
  if (bytes === undefined || bytes === null || isNaN(bytes)) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(iso: string | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  const now = Date.now()
  const diffMs = now - d.getTime()
  const diffDays = Math.floor(diffMs / 86_400_000)
  if (diffDays === 0) return 'Hoy ' + d.toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' })
  if (diffDays === 1) return 'Ayer'
  if (diffDays < 7) return `Hace ${diffDays} días`
  return d.toLocaleDateString('es', { day: 'numeric', month: 'short' })
}

function fileIconForKind(kind: string | undefined, isDirFallback: boolean) {
  if (isDirFallback || kind === 'directory') return <Folder size={16} />
  switch (kind) {
    case 'code': return <FileCode size={16} />
    case 'image': return <FileImage size={16} />
    case 'spreadsheet': return <File size={16} />
    case 'text': case 'markdown': return <FileText size={16} />
    default: return <File size={16} />
  }
}

function iconColorClass(kind: string | undefined, isDir: boolean): string {
  if (isDir || kind === 'directory') return 'arch-entry__icon--folder'
  if (kind === 'code') return 'arch-entry__icon--code'
  if (kind === 'image') return 'arch-entry__icon--image'
  return ''
}

const TEXT_KINDS = new Set(['text', 'markdown', 'code', 'log'])

// ── State machine ─────────────────────────────────────────────────────────────

type BrowseState =
  | { status: 'loading' }
  | { status: 'success'; entries: WorkspaceFile[] }
  | { status: 'error'; message: string }

// ── Breadcrumb ────────────────────────────────────────────────────────────────

interface BreadcrumbProps {
  path: string
  onNavigate: (newPath: string) => void
}

function Breadcrumb({ path, onNavigate }: BreadcrumbProps) {
  const segments = path ? path.split('/').filter(Boolean) : []

  return (
    <nav className="arch-breadcrumb" aria-label="Ruta de navegación">
      <span className="arch-breadcrumb__segment">
        <button
          type="button"
          className={`arch-breadcrumb__btn${segments.length === 0 ? ' arch-breadcrumb__btn--current' : ''}`}
          onClick={() => onNavigate('')}
        >
          Workspace
        </button>
      </span>
      {segments.map((seg, i) => {
        const segPath = segments.slice(0, i + 1).join('/')
        const isCurrent = i === segments.length - 1
        return (
          <span key={segPath} className="arch-breadcrumb__segment">
            <ChevronRight size={12} className="arch-breadcrumb__sep" aria-hidden="true" />
            <button
              type="button"
              className={`arch-breadcrumb__btn${isCurrent ? ' arch-breadcrumb__btn--current' : ''}`}
              onClick={() => !isCurrent && onNavigate(segPath)}
            >
              {seg}
            </button>
          </span>
        )
      })}
    </nav>
  )
}

// ── File/Folder entry in list view ────────────────────────────────────────────

interface EntryProps {
  entry: WorkspaceFile
  onClick: () => void
}

function ListEntry({ entry, onClick }: EntryProps) {
  const isDir = Boolean(entry.is_dir || entry.kind === 'directory')
  const colorClass = iconColorClass(entry.kind, isDir)
  return (
    <li
      className="arch-entry ds-list-item-enter"
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } }}
      aria-label={`${isDir ? 'Carpeta' : 'Archivo'}: ${entry.name}`}
    >
      <span className={`arch-entry__icon ${colorClass}`} aria-hidden="true">
        {fileIconForKind(entry.kind, isDir)}
      </span>
      <span className="arch-entry__name">{entry.name}</span>
      <span className="arch-entry__size">{isDir ? '—' : formatBytes(entry.size)}</span>
      <span className="arch-entry__date">{formatDate(entry.modified)}</span>
    </li>
  )
}

function GridEntry({ entry, onClick }: EntryProps) {
  const isDir = Boolean(entry.is_dir || entry.kind === 'directory')
  const colorClass = iconColorClass(entry.kind, isDir)
  return (
    <li
      className="arch-grid-entry ds-list-item-enter"
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } }}
      aria-label={`${isDir ? 'Carpeta' : 'Archivo'}: ${entry.name}`}
    >
      <span className={`arch-entry__icon ${colorClass}`} aria-hidden="true" style={{ fontSize: 32 }}>
        {isDir
          ? <Folder size={36} />
          : fileIconForKind(entry.kind, false)
        }
      </span>
      <span className="arch-grid-entry__name">{entry.name}</span>
    </li>
  )
}

// ── File detail drawer ────────────────────────────────────────────────────────

interface FileDrawerProps {
  file: WorkspaceFile | null
  onClose: () => void
}

function FileDrawer({ file, onClose }: FileDrawerProps) {
  const [preview, setPreview] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)

  useEffect(() => {
    if (!file || file.is_dir || !TEXT_KINDS.has(file.kind ?? '')) {
      setPreview(null)
      return
    }
    setPreviewLoading(true)
    const controller = new AbortController()
    fetch(workspaceDownloadUrl(file.path), { signal: controller.signal })
      .then(r => r.text())
      .then(text => setPreview(text.slice(0, 4000) + (text.length > 4000 ? '\n[…truncado]' : '')))
      .catch(() => setPreview(null))
      .finally(() => setPreviewLoading(false))
    return () => controller.abort()
  }, [file])

  return (
    <Drawer open={file !== null} title={file?.name ?? ''} onClose={onClose}>
      {file && (
        <div className="arch-file-meta">
          <div className="arch-file-meta__row">
            <span className="arch-file-meta__label">Tamaño</span>
            <span className="arch-file-meta__value">{formatBytes(file.size)}</span>
          </div>
          <div className="arch-file-meta__row">
            <span className="arch-file-meta__label">Tipo</span>
            <span className="arch-file-meta__value">{file.kind ?? 'archivo'}</span>
          </div>
          <div className="arch-file-meta__row">
            <span className="arch-file-meta__label">Modificado</span>
            <span className="arch-file-meta__value">{formatDate(file.modified)}</span>
          </div>
          <div className="arch-file-meta__row">
            <span className="arch-file-meta__label">Ruta</span>
            <span className="arch-file-meta__value">{file.path}</span>
          </div>

          <a
            href={workspaceDownloadUrl(file.path)}
            download={file.name}
            target="_blank"
            rel="noopener noreferrer"
            className="cv-btn cv-btn--primary cv-btn--sm"
            style={{ alignSelf: 'flex-start', marginTop: 'var(--sp-2)' }}
          >
            <Download size={14} aria-hidden="true" />
            Descargar
          </a>

          {previewLoading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', color: 'var(--ink4)', marginTop: 'var(--sp-3)' }}>
              <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />
              <span style={{ fontSize: 'var(--text-label)' }}>Cargando vista previa…</span>
            </div>
          )}

          {preview !== null && !previewLoading && (
            <pre className="arch-file-preview" aria-label="Vista previa del archivo">{preview}</pre>
          )}
        </div>
      )}
    </Drawer>
  )
}

// ── ArchivosView ──────────────────────────────────────────────────────────────

export default function ArchivosView() {
  const [currentPath, setCurrentPath] = useState('')
  const [viewMode, setViewMode] = useState<'list' | 'grid'>('list')
  const [browseState, setBrowseState] = useState<BrowseState>({ status: 'loading' })
  const [selectedFile, setSelectedFile] = useState<WorkspaceFile | null>(null)

  const load = useCallback(async (path: string) => {
    setBrowseState({ status: 'loading' })
    try {
      const raw = await listWorkspaceFiles(path || undefined)
      const entries = Array.isArray(raw) ? raw : []
      // Directories first, then files alphabetically
      entries.sort((a, b) => {
        const aDir = Boolean(a.is_dir || a.kind === 'directory')
        const bDir = Boolean(b.is_dir || b.kind === 'directory')
        if (aDir && !bDir) return -1
        if (!aDir && bDir) return 1
        return a.name.localeCompare(b.name, 'es')
      })
      setBrowseState({ status: 'success', entries })
    } catch (err) {
      setBrowseState({
        status: 'error',
        message: err instanceof Error ? err.message : 'No se pudieron cargar los archivos.',
      })
    }
  }, [])

  useEffect(() => { void load(currentPath) }, [load, currentPath])

  function navigate(path: string) {
    setCurrentPath(path)
    setSelectedFile(null)
  }

  function handleEntryClick(entry: WorkspaceFile) {
    if (entry.is_dir || entry.kind === 'directory') {
      navigate(entry.path)
    } else {
      setSelectedFile(entry)
    }
  }

  return (
    <>
      <PageHeader
        title="Archivos"
        subtitle="Espacio de trabajo del agente."
        actions={
          <Button
            variant="ghost"
            size="sm"
            onClick={() => load(currentPath)}
            aria-label="Actualizar"
            loading={browseState.status === 'loading'}
          >
            <RefreshCw size={14} aria-hidden="true" />
            Actualizar
          </Button>
        }
      />

      <div className="view-body cv-view-body">
        <div className="arch-toolbar">
          <Breadcrumb path={currentPath} onNavigate={navigate} />
          <div className="arch-view-toggle" aria-label="Modo de vista">
            <button
              type="button"
              className={`arch-view-toggle__btn${viewMode === 'list' ? ' is-active' : ''}`}
              onClick={() => setViewMode('list')}
              aria-label="Vista de lista"
              aria-pressed={viewMode === 'list'}
            >
              <List size={14} aria-hidden="true" />
            </button>
            <button
              type="button"
              className={`arch-view-toggle__btn${viewMode === 'grid' ? ' is-active' : ''}`}
              onClick={() => setViewMode('grid')}
              aria-label="Vista de cuadrícula"
              aria-pressed={viewMode === 'grid'}
            >
              <LayoutGrid size={14} aria-hidden="true" />
            </button>
          </div>
        </div>

        {browseState.status === 'loading' && (
          <div
            style={{ display: 'flex', flexDirection: 'column', gap: 2 }}
            aria-busy="true"
            aria-label="Cargando archivos…"
          >
            {[...Array(5)].map((_, i) => (
              <div key={i} className="cv-skeleton" style={{ height: 40 }} />
            ))}
          </div>
        )}

        {browseState.status === 'error' && (
          <div role="alert">
            <p className="state-error">{browseState.message}</p>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => load(currentPath)}
              style={{ marginTop: 8 }}
            >
              Reintentar
            </Button>
          </div>
        )}

        {browseState.status === 'success' && browseState.entries.length === 0 && (
          <EmptyState
            icon={<Folder size={40} />}
            title="Esta carpeta está vacía"
            description="Los archivos que cree el agente aparecerán aquí."
          />
        )}

        {browseState.status === 'success' && browseState.entries.length > 0 && (
          viewMode === 'list' ? (
            <ul
              className="arch-list"
              role="list"
              aria-label={`${browseState.entries.length} elemento${browseState.entries.length === 1 ? '' : 's'}`}
            >
              {browseState.entries.map(entry => (
                <ListEntry
                  key={entry.path}
                  entry={entry}
                  onClick={() => handleEntryClick(entry)}
                />
              ))}
            </ul>
          ) : (
            <ul
              className="arch-grid"
              role="list"
              aria-label={`${browseState.entries.length} elemento${browseState.entries.length === 1 ? '' : 's'}`}
            >
              {browseState.entries.map(entry => (
                <GridEntry
                  key={entry.path}
                  entry={entry}
                  onClick={() => handleEntryClick(entry)}
                />
              ))}
            </ul>
          )
        )}
      </div>

      <FileDrawer
        file={selectedFile}
        onClose={() => setSelectedFile(null)}
      />
    </>
  )
}
