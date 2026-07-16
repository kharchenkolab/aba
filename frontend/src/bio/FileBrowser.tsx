/**
 * FileBrowser — shared file-tree browser (the rendering core extracted from
 * FilesView). Used in two places:
 *   • Files left rail  — variant="rail", compact tree/list, promote action.
 *   • Run middle column — variant="wide", browses a Run's output subtree with
 *     pin / discuss / promote per file, and SORTABLE + RESIZABLE columns.
 *
 * Takes an already-fetched `root` tree (the caller owns fetching/reload), so the
 * same component renders the whole project tree (rail) or a run subtree (Run view).
 * Folders are first-class — nested output dirs (model/, figures/…) are navigable.
 */
import { useEffect, useMemo, useState } from 'react'
import './FilesView.css'
import type { FileNode } from '../viewers/types'

export type TreeNode = FileNode & {
  children?: TreeNode[]
  pinned?: boolean
  status?: string | null
  container_kind?: string
}

/** Per-file gestures the host wires in. Buttons render only when provided. */
export interface FileActions {
  onPromote?: (node: TreeNode) => void
  /** Toggle pin. `pinned` is the NEW state (true=pin, false=unpin). */
  onPin?: (node: TreeNode, pinned: boolean) => void
  onDiscuss?: (node: TreeNode) => void
  /** Durably retain an at-risk (in-sandbox) file — the §6.2 late-pin. */
  onKeep?: (node: TreeNode) => void
}

interface Props {
  root: TreeNode | null
  focusedId?: string
  onFocus?: (id: string) => void
  onViewFile?: (node: FileNode) => void
  variant?: 'rail' | 'wide'
  actions?: FileActions
  loading?: boolean
  error?: string | null
  emptyHint?: string
  /** Host chrome rendered into the head row (rail: the Files badge + actions). */
  titleSlot?: React.ReactNode
  actionsSlot?: React.ReactNode
  notice?: React.ReactNode
  /** Deep-link: navigate the browser INTO this folder path (list view). The
   *  nonce makes a repeat request to the same path re-fire. */
  targetPath?: string
  targetNonce?: number
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
  md: 'Markdown', csv: 'CSV', tsv: 'TSV', py: 'Python', ipynb: 'Notebook',
  json: 'JSON', yaml: 'YAML', yml: 'YAML', txt: 'Text', h5ad: 'H5AD file',
  h5: 'HDF5', rds: 'R object', pdf: 'PDF', zip: 'ZIP archive',
}
function fileTypeLabel(node: TreeNode): string {
  if (node.kind === 'readme') return 'Markdown'
  if (isImage(node)) return IMAGE_LABELS[extOf(node.artifact_path ?? node.path ?? '')] ?? 'Image'
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
  const kids = (node.children ?? []).map(c => filterTree(c, ql)).filter((c): c is TreeNode => c != null)
  const selfMatch = (node.name ?? '').toLowerCase().includes(ql)
  if (kids.length === 0 && !selfMatch && node.kind !== 'root') return null
  return { ...node, children: kids }
}
function flattenFiles(node: TreeNode, out: TreeNode[] = []): TreeNode[] {
  if (node.kind === 'file' || node.kind === 'readme') out.push(node)
  for (const c of node.children ?? []) flattenFiles(c, out)
  return out
}
function parentPath(node: TreeNode): string {
  const p = node.path ?? ''; const i = p.lastIndexOf('/'); return i > 0 ? p.slice(0, i) : ''
}
function parentOf(path: string): string {
  const i = path.lastIndexOf('/'); return i > 0 ? path.slice(0, i) : ''
}
function findNode(node: TreeNode, path: string): TreeNode | null {
  if (node.path === path) return node
  if (path === '' && node.kind === 'root') return node
  for (const c of node.children ?? []) { const f = findNode(c, path); if (f) return f }
  return null
}

type SortKey = 'name' | 'type' | 'size'

export default function FileBrowser({
  root, focusedId = '', onFocus, onViewFile, variant = 'rail', actions,
  loading, error, emptyHint, titleSlot, actionsSlot, notice, targetPath, targetNonce,
}: Props) {
  const wide = variant === 'wide'
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [view, setView] = useState<'tree' | 'list'>(wide ? 'list' : 'tree')
  const [listPath, setListPath] = useState('')
  const [sortKey, setSortKey] = useState<SortKey | null>(null)
  const [sortDir, setSortDir] = useState<1 | -1>(1)
  // Resizable Name/Type/Size columns (list/table layout). When the total exceeds
  // the panel the list scrolls horizontally, so long file names are never clipped.
  const [colW, setColW] = useState<{ name: number; type: number; size: number }>({ name: 300, type: 130, size: 78 })
  // Optimistic pinned state — pinning creates a separate entity (the file node
  // isn't marked), so we track clicked paths locally to show a filled-red pin.
  const [pinned, setPinned] = useState<Set<string>>(new Set())

  // Deep-link navigation: when a host asks to open a folder (e.g. "Browse in
  // Files tab" → a run's output dir), switch to list view at that path so it's
  // shown from the top. Keyed on the nonce so repeat clicks re-fire.
  useEffect(() => {
    if (targetPath) { setView('list'); setQuery(''); setListPath(targetPath) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetNonce])

  const visible = useMemo(() => root ? filterTree(root, query.trim()) : null, [root, query])
  const isSearching = query.trim().length > 0

  const stats = useMemo(() => {
    let files = 0, folders = 0, bytes = 0
    function walk(n: TreeNode) {
      if (n.kind === 'file' && n.artifact_path) { files += 1; bytes += n.size ?? 0 }
      else if (n.kind !== 'root' && n.kind !== 'file' && n.kind !== 'readme') folders += 1
      for (const c of n.children ?? []) walk(c)
    }
    if (visible) walk(visible)
    return { files, folders, bytes }
  }, [visible])

  const listEntries = useMemo<TreeNode[]>(() => {
    if (view !== 'list' || !visible) return []
    let entries: TreeNode[]
    if (isSearching) {
      entries = flattenFiles(visible)
    } else {
      const here = findNode(visible, listPath) ?? visible
      entries = [...(here.children ?? [])]
    }
    if (sortKey) {
      const isFolder = (n: TreeNode) => n.kind !== 'file' && n.kind !== 'readme'
      const keyVal = (n: TreeNode): string | number =>
        sortKey === 'size' ? (n.size ?? -1)
          : sortKey === 'type' ? (isFolder(n) ? folderTypeLabel(n) : fileTypeLabel(n)).toLowerCase()
            : (n.name ?? '').toLowerCase()
      entries.sort((a, b) => {
        // Folders cluster on top regardless of sort direction (navigation-friendly).
        if (isFolder(a) !== isFolder(b)) return isFolder(a) ? -1 : 1
        const va = keyVal(a), vb = keyVal(b)
        const cmp = va < vb ? -1 : va > vb ? 1 : 0
        return cmp * sortDir
      })
    } else if (isSearching) {
      entries.sort((a, b) => (a.path ?? '').localeCompare(b.path ?? ''))
    }
    return entries
  }, [view, visible, isSearching, listPath, sortKey, sortDir])
  const atRoot = listPath === ''

  function toggleFolder(path: string) {
    setCollapsed(prev => { const n = new Set(prev); n.has(path) ? n.delete(path) : n.add(path); return n })
  }
  function activate(node: TreeNode) { onViewFile?.(node) }
  function toggleSort(k: SortKey) {
    if (sortKey === k) setSortDir(d => (d === 1 ? -1 : 1))
    else { setSortKey(k); setSortDir(1) }
  }
  function startResize(col: 'name' | 'type' | 'size', e: React.MouseEvent) {
    e.preventDefault(); e.stopPropagation()
    const startX = e.clientX; const startW = colW[col]
    const maxW = col === 'name' ? 900 : 360
    const onMove = (ev: MouseEvent) => {
      // 'size' is rightmost (drag left widens); name/type widen rightward.
      const delta = col === 'size' ? (startX - ev.clientX) : (ev.clientX - startX)
      setColW(prev => ({ ...prev, [col]: Math.max(48, Math.min(maxW, startW + delta)) }))
    }
    const onUp = () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
    window.addEventListener('mousemove', onMove); window.addEventListener('mouseup', onUp)
  }

  function rowActions(node: TreeNode, isFile: boolean): React.ReactNode {
    if (!isFile || !actions) return null
    const isPinned = pinned.has(node.path)
    return (
      <>
        {actions.onPin && (
          <button className={`files__action files__action--pin ${isPinned ? 'files__action--pinned' : ''}`}
                  title={isPinned ? 'Unpin from the thread' : 'Pin to the thread'} aria-label={isPinned ? 'Unpin' : 'Pin'}
                  onClick={e => {
                    e.stopPropagation()
                    const next = !isPinned
                    setPinned(s => { const n = new Set(s); next ? n.add(node.path) : n.delete(node.path); return n })
                    actions.onPin!(node, next)
                  }}>
            <PinGlyph filled={isPinned} />
          </button>
        )}
        {actions.onDiscuss && (
          <button className="files__action files__action--discuss" title="Discuss in chat"
                  aria-label="Discuss" onClick={e => { e.stopPropagation(); actions.onDiscuss!(node) }}>
            <ChatGlyph />
          </button>
        )}
        {actions.onKeep && node.state === 'in-sandbox' && (
          <button className="files__action files__action--keep" title="Keep — retain this file durably before the sandbox is swept"
                  aria-label="Keep file" onClick={e => { e.stopPropagation(); actions.onKeep!(node) }}>
            <KeepGlyph />
          </button>
        )}
        {actions.onPromote && node.ephemeral && (
          <button className="files__action files__action--promote" title="Promote to a dataset (keep it)"
                  aria-label="Promote to dataset" onClick={e => { e.stopPropagation(); actions.onPromote!(node) }}>
            <PromoteGlyph />
          </button>
        )}
      </>
    )
  }

  // Per-file durability pill (output_durability.md §6.2). Short label in the pill,
  // the full designed badge text (e.g. "large · keeps the version at run settlement")
  // in the tooltip. Absent for nodes without a durable `state` (e.g. the project rail).
  function duraBadge(node: TreeNode): React.ReactNode {
    const st = node.state
    if (!st) return null
    const short = st === 'kept' ? (node.site ? `on ${node.site}` : 'kept ✓')
      : st === 'pinned-pending' ? 'pending'
      : st === 'in-sandbox' ? 'in sandbox'
      : st === 'cleared' ? 'cleared' : st
    return (
      <span className={`files__badge files__badge--dura files__badge--${st}`}
            title={node.badge || st}>{short}</span>
    )
  }

  function renderListRow(node: TreeNode): React.ReactNode {
    const isReadme = node.kind === 'readme'
    const isFile = node.kind === 'file' || isReadme
    const isActive = node.entity_id ? focusedId === node.entity_id : false
    const typeLabel = isFile ? fileTypeLabel(node) : folderTypeLabel(node)
    const parent = isFile && isSearching ? parentPath(node) : ''
    const hasEntity = !isFile && !!node.entity_id
    const onRowClick = () => { isFile ? activate(node) : setListPath(node.path) }
    return (
      <div key={node.path}
           className={`files__row files__row--list ${isFile ? 'files__row--file' : 'files__row--folder'} ${isReadme ? 'files__row--readme' : ''} ${node.ephemeral ? 'files__row--ephemeral' : ''} ${isActive ? 'is-current' : ''}`}
           title={node.path}>
        {isFile ? (
          <span className="files__icon">{leafIcon(node)}</span>
        ) : hasEntity ? (
          <button className="files__icon files__icon--clickable"
                  onClick={e => { e.stopPropagation(); node.entity_id && onFocus?.(node.entity_id) }}
                  title={`Focus ${node.entity_type ?? 'entity'} · ${node.entity_id}`}>{folderIcon(node)}</button>
        ) : (
          <span className="files__icon files__icon--folder">{folderIcon(node)}</span>
        )}
        <button className={`files__name files__name--clickable ${isFile ? '' : 'files__name--folder'}`}
                onClick={onRowClick}
                title={isFile ? (isReadme ? node.path : `${node.entity_type ?? ''} · ${node.entity_id ?? ''}`) : node.title || node.name}>
          <span className="files__name-main">{isFile ? node.name : `${node.name}/`}</span>
          {parent && <span className="files__name-path">{parent}</span>}
        </button>
        {isFile && duraBadge(node)}
        <span className="files__type">{typeLabel}</span>
        <span className="files__size">{isFile ? fmtSize(node.size ?? null) : <span className="files__size--dash">—</span>}</span>
        {rowActions(node, isFile)}
        {isFile && (node.artifact_path || node.synthesized || isReadme) ? (
          <a className="files__action" href={downloadUrl(node)} title="Download" download
             onClick={e => e.stopPropagation()} aria-label="Download file"><DownloadGlyph /></a>
        ) : !isFile ? (
          <a className="files__action" href={downloadUrl(node)} title="Download folder as .zip" download
             onClick={e => e.stopPropagation()} aria-label="Download folder"><DownloadGlyph /></a>
        ) : <span className="files__action files__action--ghost" />}
      </div>
    )
  }

  function renderListUpRow(): React.ReactNode {
    const parent = parentOf(listPath)
    return (
      <div key="__up__" className="files__row files__row--list files__row--up" title={parent || 'root'}>
        <span className="files__icon">↩</span>
        <button className="files__name files__name--clickable files__name--folder" onClick={() => setListPath(parent)}>
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
      return (
        <div key={node.path}
             className={`files__row files__row--file ${isReadme ? 'files__row--readme' : ''} ${node.ephemeral ? 'files__row--ephemeral' : ''} ${isActive ? 'is-current' : ''}`}
             style={indent} title={node.path}>
          <span className="files__chev files__chev--blank" />
          <span className="files__icon">{leafIcon(node)}</span>
          <button className="files__name files__name--clickable" onClick={() => activate(node)}
                  title={isReadme ? node.path : `${node.entity_type ?? ''} · ${node.entity_id ?? ''}`}>{node.name}</button>
          {duraBadge(node)}
          <span className="files__type">{fileTypeLabel(node)}</span>
          <span className="files__size">{fmtSize(node.size ?? null)}</span>
          {rowActions(node, true)}
          {(node.artifact_path || node.synthesized || isReadme) ? (
            <a className="files__action" href={downloadUrl(node)} title="Download" download
               onClick={e => e.stopPropagation()} aria-label="Download file"><DownloadGlyph /></a>
          ) : <span className="files__action files__action--ghost" />}
        </div>
      )
    }
    const showCollapsed = !isSearching && collapsed.has(node.path)
    const hasEntity = !!node.entity_id
    return (
      <div key={node.path || '__root__'}>
        {node.kind !== 'root' && (
          <div className={`files__row files__row--folder ${node.ephemeral ? 'files__row--ephemeral' : ''}`} style={indent}>
            <button className="files__chev" onClick={() => toggleFolder(node.path)}
                    aria-label={showCollapsed ? 'Expand folder' : 'Collapse folder'}><ChevronGlyph open={!showCollapsed} /></button>
            {hasEntity ? (
              <button className="files__icon files__icon--clickable" onClick={() => node.entity_id && onFocus?.(node.entity_id)}
                      title={`Focus ${node.entity_type ?? 'entity'} · ${node.entity_id}`}>{folderIcon(node)}</button>
            ) : (
              <span className="files__icon files__icon--folder">{folderIcon(node)}</span>
            )}
            <span className={`files__name files__name--folder ${hasEntity ? 'files__name--clickable' : ''}`}
                  onClick={() => hasEntity && node.entity_id && onFocus?.(node.entity_id)}
                  title={node.title || node.name}>{node.name}/</span>
            {node.ephemeral && (
              <span className="files__badge files__badge--scratch" title={node.note || 'Scratch — not kept unless promoted'}>scratch</span>
            )}
            <span className="files__type">{folderTypeLabel(node)}</span>
            <span className="files__size files__size--dash">—</span>
            <a className="files__action" href={downloadUrl(node)} title="Download folder as .zip" download
               onClick={e => e.stopPropagation()} aria-label="Download folder"><DownloadGlyph /></a>
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

  const sortCaret = (k: SortKey) => sortKey === k ? (sortDir === 1 ? ' ▲' : ' ▼') : ''
  const bodyStyle = {
    ['--col-name' as string]: `${colW.name}px`,
    ['--col-type' as string]: `${colW.type}px`,
    ['--col-size' as string]: `${colW.size}px`,
  }

  const viewToggle = (
    <div className="files__view-toggle" role="tablist" aria-label="View mode">
      <button role="tab" aria-selected={view === 'tree'} className={`files__view-btn ${view === 'tree' ? 'is-on' : ''}`}
              onClick={() => setView('tree')} title="Tree view"><TreeGlyph /><span className="files__view-label">Tree</span></button>
      <button role="tab" aria-selected={view === 'list'} className={`files__view-btn ${view === 'list' ? 'is-on' : ''}`}
              onClick={() => setView('list')} title="List view"><ListGlyph /><span className="files__view-label">List</span></button>
    </div>
  )

  return (
    <section className={`tree__index tree__index--files files-view files-view--${variant}`}>
      <div className="tree__index-head files__head">
        <div className="tree__title-row files__title-row">
          {titleSlot ?? <span className="tree__tab-badge"><FolderGlyph />Files<span className="tree__pill tree__pill--green">{stats.files}</span></span>}
          <span className="files__head-meta">{fmtSize(stats.bytes)}</span>
          <div className="files__head-actions">{viewToggle}{actionsSlot}</div>
        </div>
        <div className="files__search">
          <SearchGlyph />
          <input type="text" className="files__search-input" placeholder="Search files…"
                 value={query} onChange={e => setQuery(e.target.value)} spellCheck={false} />
          {query && <button className="files__search-clear" onClick={() => setQuery('')} title="Clear" aria-label="Clear search">×</button>}
        </div>
        {notice}
      </div>

      <div className="files__listwrap" style={view === 'list' ? bodyStyle : undefined}>
      {view === 'list' && (
        <div className={`files__column-head files__column-head--${variant}`} aria-hidden="false">
          <button className="files__col files__col--name files__col--sortable" onClick={() => toggleSort('name')}>
            Name{sortCaret('name')}<span className="files__col-grip" onMouseDown={e => startResize('name', e)} />
          </button>
          <button className="files__col files__col--type files__col--sortable" onClick={() => toggleSort('type')}>
            Type{sortCaret('type')}<span className="files__col-grip" onMouseDown={e => startResize('type', e)} />
          </button>
          <button className="files__col files__col--size files__col--sortable" onClick={() => toggleSort('size')}>
            Size{sortCaret('size')}<span className="files__col-grip" onMouseDown={e => startResize('size', e)} />
          </button>
          <span className="files__col files__col--act" />
        </div>
      )}

      <div className={`files__body files__body--${view}`}>
        {error && <div className="files__error">Couldn't load files: {error}</div>}
        {loading && !root && <div className="files__empty">Loading…</div>}
        {!loading && root && (root.children?.length ?? 0) === 0 && (
          <div className="files__empty">{emptyHint || 'Nothing here yet.'}</div>
        )}
        {!loading && visible && isSearching && (visible.children?.length ?? 0) === 0 && (
          <div className="files__empty">No files match "{query}".</div>
        )}
        {visible && view === 'tree' && renderNode(visible, 0)}
        {view === 'list' && !isSearching && !atRoot && renderListUpRow()}
        {view === 'list' && listEntries.map(renderListRow)}
        {view === 'list' && listEntries.length === 0 && root && (
          <div className="files__empty">{isSearching ? `No files match "${query}".` : 'This folder is empty.'}</div>
        )}
      </div>
      </div>

      <div className="files__footer">
        <span className="files__footer-stats">
          {stats.files} {stats.files === 1 ? 'file' : 'files'} · {stats.folders} {stats.folders === 1 ? 'folder' : 'folders'}
        </span>
        <span className="files__footer-size">{fmtSize(stats.bytes)}</span>
      </div>
    </section>
  )
}

/* Tiny inline glyphs — match the rail's stroked-SVG style. */
function ChevronGlyph({ open }: { open: boolean }) {
  return <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ transform: open ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 120ms' }}><path d="M4 2l4 4-4 4" /></svg>
}
function DownloadGlyph() {
  return <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M8 2.5v8" /><path d="M4.5 7L8 10.5 11.5 7" /><path d="M3 13.5h10" /></svg>
}
function PromoteGlyph() {
  return <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M8 10.5v-8" /><path d="M4.5 6L8 2.5 11.5 6" /><path d="M3 13.5h10" /></svg>
}
/** Shield-check — "keep this safe" (durable retain). */
function KeepGlyph() {
  return <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M8 1.5l5 2v4c0 3.2-2.1 5.4-5 6.5-2.9-1.1-5-3.3-5-6.5v-4l5-2z" /><path d="M5.8 8l1.6 1.6L10.4 6.6" /></svg>
}
function PinGlyph({ filled }: { filled?: boolean }) {
  return <svg width="13" height="13" viewBox="0 0 24 24" fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 17v5M9 3h6l-1 7 3 3H7l3-3z" /></svg>
}
function ChatGlyph() {
  return <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M2.5 4.5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H6l-3 2.5v-2.5H3.5a1 1 0 0 1-1-1Z" /></svg>
}
function SearchGlyph() {
  return <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5L13.5 13.5" /></svg>
}
function FolderGlyph() {
  return <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M2 4.5a1 1 0 0 1 1-1h3.5l1.5 1.5H13a1 1 0 0 1 1 1V12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1Z" /></svg>
}
function TreeGlyph() {
  return <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M3 3h4" /><path d="M3 8h6" /><path d="M3 13h8" /><path d="M3 3v10" /></svg>
}
function ListGlyph() {
  return <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M3 4h10" /><path d="M3 8h10" /><path d="M3 12h10" /></svg>
}
