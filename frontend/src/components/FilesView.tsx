/**
 * FilesView — nested project file tree (files.md §3.3, §6).
 *
 * Renders the multi-rooted virtual tree composed by the backend
 * (threads → runs/results/claims, runs → child files, results → member
 * files). Same canonical artifact may appear at multiple paths.
 * Click a file → focus its entity. Click a folder name → expand/collapse;
 * if the folder is backed by an entity, click the entity icon to focus it.
 * Each folder + file has a ⬇ that downloads via the path-based endpoint.
 *
 * Layout adapts via CSS container queries: a narrow rail shows just
 * name + download, a wider rail reveals Type / Size columns and a
 * column-header row, modeled on the mockup at misc/aspects.md.
 */
import { useEffect, useMemo, useState } from 'react'
import './FilesView.css'
import type { FileNode } from '../viewers/types'

type TreeNode = FileNode & {
  children?: TreeNode[]
  pinned?: boolean
  status?: string | null
  container_kind?: string
}

interface Props {
  focusedId: string
  onFocus: (id: string) => void
  onViewFile?: (node: FileNode) => void   // central-column viewer for synthesized files
  reloadKey?: unknown
}

function fmtSize(n: number | null | undefined): string {
  if (n == null) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`
}

function extOf(path: string): string {
  const m = /\.([a-z0-9]+)$/i.exec(path)
  return m ? m[1].toLowerCase() : ''
}

function isImage(node: TreeNode): boolean {
  const p = node.artifact_path
  return !!p && /\.(png|jpe?g|gif|svg|webp)$/i.test(p)
}

const IMAGE_LABELS: Record<string, string> = {
  png: 'PNG image', jpg: 'JPEG image', jpeg: 'JPEG image',
  gif: 'GIF image', svg: 'SVG image', webp: 'WebP image',
}
const FILE_LABELS: Record<string, string> = {
  md: 'Markdown', csv: 'CSV', tsv: 'TSV',
  py: 'Python', ipynb: 'Notebook', json: 'JSON',
  yaml: 'YAML', yml: 'YAML', txt: 'Text',
  h5ad: 'H5AD file', h5: 'HDF5',
  pdf: 'PDF', zip: 'ZIP archive',
}
function fileTypeLabel(node: TreeNode): string {
  if (node.kind === 'readme') return 'Markdown'
  if (isImage(node)) {
    const e = extOf(node.artifact_path ?? node.path ?? '')
    return IMAGE_LABELS[e] ?? 'Image'
  }
  if (node.kind === 'file') {
    const e = extOf(node.artifact_path ?? node.path ?? '')
    if (FILE_LABELS[e]) return FILE_LABELS[e]
    if (e) return e.toUpperCase()
  }
  return ''
}
function folderTypeLabel(node: TreeNode): string {
  switch (node.entity_type) {
    case 'thread': return 'Thread'
    case 'analysis': return 'Run'
    case 'result': return 'Result set'
    case 'finding': return 'Finding'
    case 'dataset': return 'Dataset'
    case 'claim': return 'Claim'
    default: return 'Folder'
  }
}

function leafIcon(node: TreeNode): string {
  if (node.kind === 'readme') return '📄'
  if (isImage(node)) return '🖼'
  if (node.entity_type === 'note' || node.entity_type === 'narrative' || node.entity_type === 'claim') return '📝'
  if (node.entity_type === 'table') return '📊'
  if (node.entity_type === 'dataset') return '📚'
  if (node.entity_type === 'code') return '🐍'
  return '📄'
}

function folderIcon(node: TreeNode): string {
  if (node.entity_type === 'thread') return '🧵'
  if (node.entity_type === 'analysis') return '⚙'
  if (node.entity_type === 'result') return '🔖'
  if (node.entity_type === 'finding') return '🏷'
  return '📁'
}

function downloadUrl(node: TreeNode): string {
  if (node.kind === 'file' && node.entity_id && node.artifact_path) {
    return `/api/entities/${encodeURIComponent(node.entity_id)}/download`
  }
  return `/api/files/download?path=${encodeURIComponent(node.path)}`
}

function filterTree(node: TreeNode, q: string): TreeNode | null {
  if (!q) return node
  const ql = q.toLowerCase()
  if (node.kind === 'file' || node.kind === 'readme') {
    return node.name.toLowerCase().includes(ql) ? node : null
  }
  const kids = (node.children ?? [])
    .map(c => filterTree(c, ql))
    .filter((c): c is TreeNode => c != null)
  const selfMatch = (node.name ?? '').toLowerCase().includes(ql)
  if (kids.length === 0 && !selfMatch && node.kind !== 'root') return null
  return { ...node, children: kids }
}

/** Flatten a tree to its leaf files (skips folders / root). */
function flattenFiles(node: TreeNode, out: TreeNode[] = []): TreeNode[] {
  if (node.kind === 'file' || node.kind === 'readme') {
    out.push(node)
  }
  for (const c of node.children ?? []) flattenFiles(c, out)
  return out
}

/** Parent path for a node (everything before the last `/`). */
function parentPath(node: TreeNode): string {
  const p = node.path ?? ''
  const i = p.lastIndexOf('/')
  return i > 0 ? p.slice(0, i) : ''
}

function parentOf(path: string): string {
  const i = path.lastIndexOf('/')
  return i > 0 ? path.slice(0, i) : ''
}

/** Locate a node by its `.path` (DFS). Empty string returns the root. */
function findNode(node: TreeNode, path: string): TreeNode | null {
  if (node.path === path) return node
  if (path === '' && node.kind === 'root') return node
  for (const c of node.children ?? []) {
    const f = findNode(c, path)
    if (f) return f
  }
  return null
}

export default function FilesView({ focusedId, onFocus, onViewFile, reloadKey }: Props) {
  const [root, setRoot] = useState<TreeNode | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [materializeMsg, setMaterializeMsg] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [view, setView] = useState<'tree' | 'list'>('tree')
  const [listPath, setListPath] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)
    fetch('/api/files/tree')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`${r.status}`)))
      .then(d => { if (!cancelled) setRoot(d as TreeNode) })
      .catch(e => { if (!cancelled) setError(String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [reloadKey])

  const stats = useMemo(() => {
    let files = 0, folders = 0, bytes = 0
    function walk(n: TreeNode) {
      if (n.kind === 'file' && n.artifact_path) { files += 1; bytes += n.size ?? 0 }
      else if (n.kind !== 'root' && n.kind !== 'file' && n.kind !== 'readme') folders += 1
      for (const c of n.children ?? []) walk(c)
    }
    if (root) walk(root)
    return { files, folders, bytes }
  }, [root])

  const visible = useMemo(() => root ? filterTree(root, query.trim()) : null, [root, query])
  const isSearching = query.trim().length > 0

  // List mode produces either a folder-at-a-time browse view (no query) or
  // a flat search-result list (query active).
  const listEntries = useMemo<TreeNode[]>(() => {
    if (view !== 'list' || !visible) return []
    if (isSearching) {
      const all = flattenFiles(visible)
      all.sort((a, b) => (a.path ?? '').localeCompare(b.path ?? ''))
      return all
    }
    const here = findNode(visible, listPath) ?? visible
    return [...(here.children ?? [])]
  }, [view, visible, isSearching, listPath])
  const atRoot = listPath === ''

  function toggleFolder(path: string) {
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path); else next.add(path)
      return next
    })
  }

  async function materialize() {
    setMaterializeMsg('Building folder…')
    try {
      const pid = await (await fetch('/api/projects/current')).json().then(d => d.current)
      if (!pid) { setMaterializeMsg('No active project.'); return }
      const r = await fetch(`/api/projects/${encodeURIComponent(pid)}/materialize?clean=true`, { method: 'POST' })
      const d = await r.json()
      const path = d.out_dir as string
      const linked = d.linked ?? 0, copied = d.copied ?? 0, missing = d.missing ?? 0
      setMaterializeMsg(
        `Built ${path}: ${linked} linked, ${copied} copied${missing ? `, ${missing} missing` : ''}.`,
      )
    } catch (e) {
      setMaterializeMsg(`Failed: ${String(e)}`)
    }
  }

  function activate(node: TreeNode) {
    if (onViewFile) onViewFile(node)
  }

  function renderListRow(node: TreeNode): React.ReactNode {
    const isReadme = node.kind === 'readme'
    const isFile = node.kind === 'file' || isReadme
    const isActive = node.entity_id ? focusedId === node.entity_id : false
    // Files: show their type. Folders: show their folder kind (Thread, Run, …).
    const typeLabel = isFile ? fileTypeLabel(node) : folderTypeLabel(node)
    const parent = isFile && isSearching ? parentPath(node) : ''
    const hasEntity = !isFile && !!node.entity_id

    function onRowClick() {
      if (isFile) activate(node)
      else setListPath(node.path)
    }

    return (
      <div
        key={node.path}
        className={`files__row files__row--list ${isFile ? 'files__row--file' : 'files__row--folder'} ${isReadme ? 'files__row--readme' : ''} ${isActive ? 'is-current' : ''}`}
        title={node.path}
      >
        {isFile ? (
          <span className="files__icon">{leafIcon(node)}</span>
        ) : hasEntity ? (
          <button
            className="files__icon files__icon--clickable"
            onClick={e => { e.stopPropagation(); node.entity_id && onFocus(node.entity_id) }}
            title={`Focus ${node.entity_type ?? 'entity'} · ${node.entity_id}`}
          >{folderIcon(node)}</button>
        ) : (
          <span className="files__icon files__icon--folder">{folderIcon(node)}</span>
        )}
        <button
          className={`files__name files__name--clickable ${isFile ? '' : 'files__name--folder'}`}
          onClick={onRowClick}
          title={isFile ? (isReadme ? node.path : `${node.entity_type ?? ''} · ${node.entity_id ?? ''}`) : node.title || node.name}
        >
          <span className="files__name-main">{isFile ? node.name : `${node.name}/`}</span>
          {parent && <span className="files__name-path">{parent}</span>}
        </button>
        <span className="files__type">{typeLabel}</span>
        <span className="files__size">{isFile ? fmtSize(node.size ?? null) : <span className="files__size--dash">—</span>}</span>
        {isFile && (node.artifact_path || node.synthesized || isReadme) ? (
          <a
            className="files__action"
            href={downloadUrl(node)}
            title="Download"
            download
            onClick={e => e.stopPropagation()}
            aria-label="Download file"
          >
            <DownloadGlyph />
          </a>
        ) : !isFile ? (
          <a
            className="files__action"
            href={downloadUrl(node)}
            title="Download folder as .zip"
            download
            onClick={e => e.stopPropagation()}
            aria-label="Download folder"
          >
            <DownloadGlyph />
          </a>
        ) : <span className="files__action files__action--ghost" />}
      </div>
    )
  }

  function renderListUpRow(): React.ReactNode {
    const parent = parentOf(listPath)
    return (
      <div
        key="__up__"
        className="files__row files__row--list files__row--up"
        title={parent || 'Project root'}
      >
        <span className="files__icon">↩</span>
        <button
          className="files__name files__name--clickable files__name--folder"
          onClick={() => setListPath(parent)}
        >
          <span className="files__name-main">../</span>
          <span className="files__name-path">in {listPath}</span>
        </button>
        <span className="files__type">Up one level</span>
        <span className="files__size files__size--dash">—</span>
        <span className="files__action files__action--ghost" />
      </div>
    )
  }

  function renderNode(node: TreeNode, depth: number): React.ReactNode {
    const indent = { paddingLeft: `${8 + depth * 14}px` }
    if (node.kind === 'readme' || node.kind === 'file') {
      const isReadme = node.kind === 'readme'
      const isActive = node.entity_id ? focusedId === node.entity_id : false
      const typeLabel = fileTypeLabel(node)
      return (
        <div
          key={node.path}
          className={`files__row files__row--file ${isReadme ? 'files__row--readme' : ''} ${isActive ? 'is-current' : ''}`}
          style={indent}
          title={node.path}
        >
          <span className="files__chev files__chev--blank" />
          <span className="files__icon">{leafIcon(node)}</span>
          <button
            className="files__name files__name--clickable"
            onClick={() => activate(node)}
            title={isReadme ? node.path : `${node.entity_type ?? ''} · ${node.entity_id ?? ''}`}
          >{node.name}</button>
          <span className="files__type">{typeLabel}</span>
          <span className="files__size">{fmtSize(node.size ?? null)}</span>
          {(node.artifact_path || node.synthesized || isReadme) ? (
            <a
              className="files__action"
              href={downloadUrl(node)}
              title="Download"
              download
              onClick={e => e.stopPropagation()}
              aria-label="Download file"
            >
              <DownloadGlyph />
            </a>
          ) : <span className="files__action files__action--ghost" />}
        </div>
      )
    }
    // Folder (or root)
    const showCollapsed = !isSearching && collapsed.has(node.path)
    const hasEntity = !!node.entity_id
    return (
      <div key={node.path || '__root__'}>
        {node.kind !== 'root' && (
          <div className="files__row files__row--folder" style={indent}>
            <button
              className="files__chev"
              onClick={() => toggleFolder(node.path)}
              aria-label={showCollapsed ? 'Expand folder' : 'Collapse folder'}
            >
              <ChevronGlyph open={!showCollapsed} />
            </button>
            {hasEntity ? (
              <button
                className="files__icon files__icon--clickable"
                onClick={() => node.entity_id && onFocus(node.entity_id)}
                title={`Focus ${node.entity_type ?? 'entity'} · ${node.entity_id}`}
              >{folderIcon(node)}</button>
            ) : (
              <span className="files__icon files__icon--folder">{folderIcon(node)}</span>
            )}
            <span
              className={`files__name files__name--folder ${hasEntity ? 'files__name--clickable' : ''}`}
              onClick={() => hasEntity && node.entity_id && onFocus(node.entity_id)}
              title={node.title || node.name}
            >{node.name}/</span>
            <span className="files__type">{folderTypeLabel(node)}</span>
            <span className="files__size files__size--dash">—</span>
            <a
              className="files__action"
              href={downloadUrl(node)}
              title="Download folder as .zip"
              download
              onClick={e => e.stopPropagation()}
              aria-label="Download folder"
            >
              <DownloadGlyph />
            </a>
          </div>
        )}
        {!showCollapsed && node.children && (
          <div className="files__children">
            {node.children.map(c => renderNode(c, node.kind === 'root' ? 0 : depth + 1))}
          </div>
        )}
      </div>
    )
  }

  return (
    <section className="tree__index tree__index--files files-view">
      <div className="tree__index-head files__head">
        <div className="tree__title-row files__title-row">
          <span className="tree__tab-badge">
            <FolderGlyph />
            Files
            <span className="tree__pill tree__pill--green">{stats.files}</span>
          </span>
          <span className="files__head-meta">{fmtSize(stats.bytes)}</span>
          <div className="files__head-actions">
            <div className="files__view-toggle" role="tablist" aria-label="View mode">
              <button
                role="tab"
                aria-selected={view === 'tree'}
                className={`files__view-btn ${view === 'tree' ? 'is-on' : ''}`}
                onClick={() => setView('tree')}
                title="Tree view"
              >
                <TreeGlyph /><span className="files__view-label">Tree</span>
              </button>
              <button
                role="tab"
                aria-selected={view === 'list'}
                className={`files__view-btn ${view === 'list' ? 'is-on' : ''}`}
                onClick={() => setView('list')}
                title="List view"
              >
                <ListGlyph /><span className="files__view-label">List</span>
              </button>
            </div>
            <a
              className="files__icon-btn"
              href="/api/files/download"
              title="Download the whole tree as .zip"
              download
              aria-label="Download all"
            >
              <DownloadGlyph />
            </a>
            <button
              className="files__icon-btn"
              onClick={materialize}
              title="Materialize on disk (symlinks → canonical artifacts)"
              aria-label="Materialize folder"
            >
              <KebabGlyph />
            </button>
          </div>
        </div>
        <div className="files__search">
          <SearchGlyph />
          <input
            type="text"
            className="files__search-input"
            placeholder="Search files…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            spellCheck={false}
          />
          {query && (
            <button
              className="files__search-clear"
              onClick={() => setQuery('')}
              title="Clear search"
              aria-label="Clear search"
            >×</button>
          )}
        </div>
        {materializeMsg && <div className="files__notice">{materializeMsg}</div>}
      </div>

      <div className="files__column-head" aria-hidden="true">
        <span className="files__col files__col--name">Name</span>
        <span className="files__col files__col--type">Type</span>
        <span className="files__col files__col--size">Size</span>
        <span className="files__col files__col--act" />
      </div>

      <div className={`files__body files__body--${view}`}>
        {error && <div className="files__error">Couldn't load files: {error}</div>}
        {loading && !root && <div className="files__empty">Loading…</div>}
        {!loading && root && (root.children?.length ?? 0) === 0 && (
          <div className="files__empty">No artifacts yet — run an analysis to populate the tree.</div>
        )}
        {!loading && visible && isSearching && (visible.children?.length ?? 0) === 0 && (
          <div className="files__empty">No files match "{query}".</div>
        )}
        {visible && view === 'tree' && renderNode(visible, 0)}
        {view === 'list' && !isSearching && !atRoot && renderListUpRow()}
        {view === 'list' && listEntries.map(renderListRow)}
        {view === 'list' && listEntries.length === 0 && root && (
          <div className="files__empty">
            {isSearching ? `No files match "${query}".` : 'This folder is empty.'}
          </div>
        )}
      </div>

      {root && (
        <div className="files__footer">
          <span className="files__footer-stats">
            {stats.files} {stats.files === 1 ? 'file' : 'files'} · {stats.folders} {stats.folders === 1 ? 'folder' : 'folders'}
          </span>
          <span className="files__footer-size">{fmtSize(stats.bytes)}</span>
        </div>
      )}
    </section>
  )
}

/* Tiny inline glyphs — match the rest of the rail's stroked-SVG style. */
function ChevronGlyph({ open }: { open: boolean }) {
  return (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ transform: open ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 120ms' }}>
      <path d="M4 2l4 4-4 4" />
    </svg>
  )
}
function DownloadGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 2.5v8" />
      <path d="M4.5 7L8 10.5 11.5 7" />
      <path d="M3 13.5h10" />
    </svg>
  )
}
function FolderGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 4.5a1 1 0 0 1 1-1h3.5l1.5 1.5H13a1 1 0 0 1 1 1V12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4.5Z" />
    </svg>
  )
}
function SearchGlyph() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.5 10.5L13.5 13.5" />
    </svg>
  )
}
function KebabGlyph() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
      <circle cx="8" cy="3.5" r="1.2" />
      <circle cx="8" cy="8" r="1.2" />
      <circle cx="8" cy="12.5" r="1.2" />
    </svg>
  )
}
function TreeGlyph() {
  return (
    <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 3h4" /><path d="M3 8h6" /><path d="M3 13h8" />
      <path d="M3 3v10" />
    </svg>
  )
}
function ListGlyph() {
  return (
    <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 4h10" /><path d="M3 8h10" /><path d="M3 12h10" />
    </svg>
  )
}
