/**
 * ArchivosView — macOS Finder-style workspace file browser.
 *
 * API:
 *   GET /api/v1/workspace/files?path=<relpath>  →  WorkspaceFile[]
 *   GET /api/v1/workspace/download?path=<relpath>  →  binary download
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { sileo } from 'sileo'
import {
  Folder, FileText, FileCode, FileImage, File,
  LayoutGrid, List, RefreshCw, Download, ChevronRight,
  Loader2, Upload,
} from 'lucide-react'
import { listWorkspaceFiles, workspaceDownloadUrl, uploadWorkspaceFile } from '../api/client'
import type { WorkspaceFile } from '../api/types'
import { Drawer } from '../components/ui/Drawer'
import { EmptyState } from '../components/ui/EmptyState'
import { PageHeader } from '../components/ui/PageHeader'
import { Button } from '../components/ui/Button'
import {
  AnimatePresence,
  AnimatedListItem,
  FadeIn,
  HoverRow,
  motion,
  TWEEN,
} from '../components/ui/motion'
import styles from './ArchivosView.module.css'

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

type FileKind = string | undefined

function fileIconForKind(kind: FileKind, isDirFallback: boolean) {
  if (isDirFallback || kind === 'directory') return <Folder size={16} />
  switch (kind) {
    case 'code':     return <FileCode size={16} />
    case 'image':    return <FileImage size={16} />
    case 'text':
    case 'markdown': return <FileText size={16} />
    default:         return <File size={16} />
  }
}

function iconColorClass(kind: FileKind, isDir: boolean): string {
  if (isDir || kind === 'directory') return styles.entryIconFolder
  if (kind === 'code')               return styles.entryIconCode
  if (kind === 'image')              return styles.entryIconImage
  return ''
}

const TEXT_KINDS = new Set(['text', 'markdown', 'code', 'log'])

// ── State machine ─────────────────────────────────────────────────────────────

type BrowseState =
  | { status: 'loading' }
  | { status: 'success'; entries: WorkspaceFile[]; path: string }
  | { status: 'error'; message: string }

// ── Skeleton — mirrors the final list layout so there is no layout shift ──────

function ListSkeleton() {
  // Name widths cycle to avoid a uniform-row look
  const widths = ['62%', '44%', '55%', '38%', '68%', '50%']
  return (
    <div
      className={styles.skeletonList}
      aria-busy="true"
      aria-label="Cargando archivos…"
      role="status"
    >
      {widths.map((w, i) => (
        <div
          key={i}
          className={styles.skeletonRow}
          style={{ animationDelay: `${i * 60}ms` }}
        >
          <span
            className={`skeleton ${styles.skeletonIcon}`}
            style={{ animationDelay: `${i * 60}ms` }}
          />
          <span
            className={`skeleton ${styles.skeletonName}`}
            style={{ width: w, animationDelay: `${i * 60 + 20}ms` }}
          />
          <span
            className={`skeleton ${styles.skeletonSize}`}
            style={{ animationDelay: `${i * 60 + 30}ms` }}
          />
          <span
            className={`skeleton ${styles.skeletonDate}`}
            style={{ animationDelay: `${i * 60 + 40}ms` }}
          />
        </div>
      ))}
    </div>
  )
}

// ── Breadcrumb ────────────────────────────────────────────────────────────────

interface BreadcrumbProps {
  path: string
  onNavigate: (newPath: string) => void
}

function Breadcrumb({ path, onNavigate }: BreadcrumbProps) {
  const segments = path ? path.split('/').filter(Boolean) : []

  return (
    <nav className={styles.breadcrumb} aria-label="Ruta de navegación">
      <span className={styles.breadcrumbSegment}>
        <button
          type="button"
          className={`${styles.breadcrumbBtn}${segments.length === 0 ? ` ${styles.breadcrumbCurrent}` : ''}`}
          onClick={() => onNavigate('')}
          aria-current={segments.length === 0 ? 'page' : undefined}
        >
          Workspace
        </button>
      </span>
      {segments.map((seg, i) => {
        const segPath = segments.slice(0, i + 1).join('/')
        const isCurrent = i === segments.length - 1
        return (
          <span key={segPath} className={styles.breadcrumbSegment}>
            <ChevronRight
              size={11}
              className={styles.breadcrumbSep}
              aria-hidden="true"
            />
            <button
              type="button"
              className={`${styles.breadcrumbBtn}${isCurrent ? ` ${styles.breadcrumbCurrent}` : ''}`}
              onClick={() => !isCurrent && onNavigate(segPath)}
              aria-current={isCurrent ? 'page' : undefined}
            >
              {seg}
            </button>
          </span>
        )
      })}
    </nav>
  )
}

// ── File/Folder entry — list view ─────────────────────────────────────────────

interface EntryProps {
  entry: WorkspaceFile
  onClick: () => void
}

function ListEntry({ entry, onClick }: EntryProps) {
  const isDir = Boolean(entry.is_dir || entry.kind === 'directory')
  const colorClass = iconColorClass(entry.kind, isDir)
  return (
    <HoverRow
      className={styles.entry}
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() }
      }}
      aria-label={`${isDir ? 'Carpeta' : 'Archivo'}: ${entry.name}`}
    >
      <span className={`${styles.entryIcon} ${colorClass}`} aria-hidden="true">
        {fileIconForKind(entry.kind, isDir)}
      </span>
      <span className={`${styles.entryName}${isDir ? ` ${styles.entryNameDir}` : ''}`}>
        {entry.name}
      </span>
      <span className={styles.entrySize}>{isDir ? '—' : formatBytes(entry.size)}</span>
      <span className={styles.entryDate}>{formatDate(entry.modified)}</span>
    </HoverRow>
  )
}

// ── File/Folder entry — grid view ─────────────────────────────────────────────

function GridEntry({ entry, onClick }: EntryProps) {
  const isDir = Boolean(entry.is_dir || entry.kind === 'directory')
  const colorClass = iconColorClass(entry.kind, isDir)
  return (
    <HoverRow
      className={styles.gridEntry}
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() }
      }}
      aria-label={`${isDir ? 'Carpeta' : 'Archivo'}: ${entry.name}`}
    >
      <span className={`${styles.entryIcon} ${colorClass}`} aria-hidden="true" style={{ width: 'auto', height: 'auto' }}>
        {isDir
          ? <Folder size={36} />
          : fileIconForKind(entry.kind, false)
        }
      </span>
      <span className={styles.gridEntryName}>{entry.name}</span>
    </HoverRow>
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
        <div className={styles.fileMeta}>
          <div className={styles.fileMetaRows}>
            <div className={styles.fileMetaRow}>
              <span className={styles.fileMetaLabel}>Tamaño</span>
              <span className={styles.fileMetaValue}>{formatBytes(file.size)}</span>
            </div>
            <div className={styles.fileMetaRow}>
              <span className={styles.fileMetaLabel}>Tipo</span>
              <span className={styles.fileMetaValue}>{file.kind ?? 'archivo'}</span>
            </div>
            <div className={styles.fileMetaRow}>
              <span className={styles.fileMetaLabel}>Modificado</span>
              <span className={styles.fileMetaValue}>{formatDate(file.modified)}</span>
            </div>
            <div className={styles.fileMetaRow}>
              <span className={styles.fileMetaLabel}>Ruta</span>
              <span className={styles.fileMetaValue}>{file.path}</span>
            </div>
          </div>

          <a
            href={workspaceDownloadUrl(file.path)}
            download={file.name}
            target="_blank"
            rel="noopener noreferrer"
            className="cv-btn cv-btn--primary cv-btn--sm"
            style={{ alignSelf: 'flex-start' }}
          >
            <Download size={13} aria-hidden="true" />
            Descargar archivo
          </a>

          {previewLoading && (
            <div className={styles.previewLoading}>
              <Loader2 size={13} className="spin" aria-hidden="true" />
              <span>Cargando vista previa…</span>
            </div>
          )}

          {preview !== null && !previewLoading && (
            <pre className={styles.preview} aria-label="Vista previa del contenido">
              {preview}
            </pre>
          )}
        </div>
      )}
    </Drawer>
  )
}

// ── List header row ───────────────────────────────────────────────────────────

function ListColumnHeader() {
  return (
    <div className={styles.listHeader} aria-hidden="true">
      <span style={{ width: 20, flexShrink: 0 }} />
      <span className={styles.listHeaderName}>Nombre</span>
      <span className={styles.listHeaderSize}>Tamaño</span>
      <span className={styles.listHeaderDate}>Modificado</span>
    </div>
  )
}

// ── ArchivosView ──────────────────────────────────────────────────────────────

export default function ArchivosView() {
  const [currentPath, setCurrentPath] = useState('')
  const [viewMode, setViewMode] = useState<'list' | 'grid'>('list')
  const [browseState, setBrowseState] = useState<BrowseState>({ status: 'loading' })
  const [selectedFile, setSelectedFile] = useState<WorkspaceFile | null>(null)
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

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
      setBrowseState({ status: 'success', entries, path })
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

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = e.target.files
    if (!picked || picked.length === 0) return
    setUploading(true)
    let ok = 0
    for (const f of Array.from(picked)) {
      try { await uploadWorkspaceFile(f); ok += 1 }
      catch (err) {
        sileo.error({ title: `No se pudo subir «${f.name}»`, description: err instanceof Error ? err.message : undefined })
      }
    }
    setUploading(false)
    e.target.value = '' // reset so the same file can be re-selected
    if (ok > 0) {
      sileo.success({ title: ok === 1 ? 'Archivo subido' : `${ok} archivos subidos` })
      // Uploads land at the workspace root — go there so the file is visible.
      navigate('')
      void load('')
    }
  }

  function handleEntryClick(entry: WorkspaceFile) {
    if (entry.is_dir || entry.kind === 'directory') {
      navigate(entry.path)
    } else {
      setSelectedFile(entry)
    }
  }

  // Stable key for the entry list so AnimatePresence triggers a cross-fade
  // when the user enters a different folder.
  const listKey = browseState.status === 'success' ? browseState.path : '__loading__'
  const entryCount = browseState.status === 'success' ? browseState.entries.length : 0

  return (
    <>
      <PageHeader
        title="Archivos"
        subtitle="Espacio de trabajo del agente."
        actions={
          <>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              onChange={handleUpload}
              aria-hidden="true"
            />
            <Button
              variant="secondary"
              size="sm"
              onClick={() => fileInputRef.current?.click()}
              aria-label="Subir archivos al espacio de trabajo"
              loading={uploading}
            >
              <Upload size={13} aria-hidden="true" />
              Subir
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => load(currentPath)}
              aria-label="Actualizar lista de archivos"
              loading={browseState.status === 'loading'}
            >
              <RefreshCw size={13} aria-hidden="true" />
              Actualizar
            </Button>
          </>
        }
      />

      <div className="view-body cv-view-body">
        {/* Toolbar: breadcrumb + view toggle */}
        <div className={styles.toolbar}>
          <Breadcrumb path={currentPath} onNavigate={navigate} />

          <div
            className={styles.viewToggle}
            role="group"
            aria-label="Modo de vista"
          >
            <button
              type="button"
              className={`${styles.viewToggleBtn}${viewMode === 'list' ? ` ${styles.viewToggleBtnActive}` : ''}`}
              onClick={() => setViewMode('list')}
              aria-label="Vista de lista"
              aria-pressed={viewMode === 'list'}
            >
              <List size={13} aria-hidden="true" />
            </button>
            <button
              type="button"
              className={`${styles.viewToggleBtn}${viewMode === 'grid' ? ` ${styles.viewToggleBtnActive}` : ''}`}
              onClick={() => setViewMode('grid')}
              aria-label="Vista de cuadrícula"
              aria-pressed={viewMode === 'grid'}
            >
              <LayoutGrid size={13} aria-hidden="true" />
            </button>
          </div>
        </div>

        {/* Loading — skeleton rows mirroring the list layout */}
        {browseState.status === 'loading' && <ListSkeleton />}

        {/* Error with inline retry */}
        {browseState.status === 'error' && (
          <FadeIn>
            <div role="alert" className={styles.errorState}>
              <p className={styles.errorMessage}>{browseState.message}</p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => load(currentPath)}
              >
                Reintentar
              </Button>
            </div>
          </FadeIn>
        )}

        {/* AnimatePresence key on listKey cross-fades when navigating folders */}
        <AnimatePresence mode="wait">
          {browseState.status === 'success' && (
            <motion.div
              key={listKey}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={TWEEN}
            >
              {browseState.entries.length === 0 ? (
                <EmptyState
                  icon={<Folder size={40} />}
                  title="Esta carpeta está vacía"
                  description="Los archivos que cree el agente aparecen aquí. También puedes subir los tuyos con el botón Subir."
                />
              ) : (
                <>
                  {/* Accessible count badge below toolbar */}
                  <p className={styles.countLabel}>
                    {entryCount} elemento{entryCount === 1 ? '' : 's'}
                  </p>

                  {viewMode === 'list' ? (
                    <>
                      <ListColumnHeader />
                      <ul
                        className={styles.list}
                        role="list"
                        aria-label={`${entryCount} elemento${entryCount === 1 ? '' : 's'}`}
                      >
                        <AnimatePresence initial={false}>
                          {browseState.entries.map(entry => (
                            <AnimatedListItem key={entry.path}>
                              <ListEntry
                                entry={entry}
                                onClick={() => handleEntryClick(entry)}
                              />
                            </AnimatedListItem>
                          ))}
                        </AnimatePresence>
                      </ul>
                    </>
                  ) : (
                    <ul
                      className={styles.grid}
                      role="list"
                      aria-label={`${entryCount} elemento${entryCount === 1 ? '' : 's'}`}
                    >
                      <AnimatePresence initial={false}>
                        {browseState.entries.map(entry => (
                          <AnimatedListItem key={entry.path}>
                            <GridEntry
                              entry={entry}
                              onClick={() => handleEntryClick(entry)}
                            />
                          </AnimatedListItem>
                        ))}
                      </AnimatePresence>
                    </ul>
                  )}
                </>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <FileDrawer
        file={selectedFile}
        onClose={() => setSelectedFile(null)}
      />
    </>
  )
}
