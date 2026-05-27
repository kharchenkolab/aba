/**
 * FilesView — virtual file tree projection of the entity graph
 * (files.md §6 of the filesystem plan).
 *
 * Fetches /api/files/tree (flat list with computed display_paths),
 * groups by directory, renders as a collapsible folder tree. Click a
 * file → focus the entity. Each folder + each file gets a download
 * button (F4 wires the actual download).
 */
import { useEffect, useMemo, useState } from 'react'
import './FilesView.css'

interface FileItem {
  entity_id: string
  type: string
  title: string
  status: string
  display_path: string
  artifact_path: string | null
  size: number | null
  created_at: string
  pinned: boolean
}

interface Props {
  focusedId: string
  onFocus: (id: string) => void
  reloadKey?: unknown          // bump to refetch (project switch, refresh)
}

type TreeNode = {
  name: string
  path: string                 // full display path up to here
  children: Map<string, TreeNode>
  file?: FileItem              // present at leaf
}

function buildTree(items: FileItem[]): TreeNode {
  const root: TreeNode = { name: '', path: '', children: new Map() }
  for (const item of items) {
    const parts = item.display_path.split('/').filter(Boolean)
    const isDir = item.display_path.endsWith('/')
    let cur = root
    parts.forEach((part, i) => {
      const isLeaf = i === parts.length - 1
      let child = cur.children.get(part)
      if (!child) {
        child = {
          name: part,
          path: parts.slice(0, i + 1).join('/') + (isLeaf && isDir ? '/' : ''),
          children: new Map(),
        }
        cur.children.set(part, child)
      }
      if (isLeaf) {
        if (isDir) {
          // Entity is a directory (e.g., a result). Attach the entity
          // to the directory node so a click on the folder header opens it.
          child.file = item
        } else {
          child.file = item
        }
      }
      cur = child
    })
  }
  return root
}

function fmtSize(n: number | null): string {
  if (n == null) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`
}

function isImage(item: FileItem | undefined): boolean {
  if (!item || !item.artifact_path) return false
  return /\.(png|jpe?g|gif|svg|webp)$/i.test(item.artifact_path)
}

function downloadUrl(item: FileItem): string {
  // Honors the existing GET /api/entities/{id}/download route.
  return `/api/entities/${encodeURIComponent(item.entity_id)}/download`
}

function folderDownloadUrl(path: string): string {
  // F4: wires to a zip-archive endpoint. Path is the folder's display path.
  return `/api/files/download?path=${encodeURIComponent(path)}`
}

export default function FilesView({ focusedId, onFocus, reloadKey }: Props) {
  const [items, setItems] = useState<FileItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Folder expansion state, keyed by path. Default: all expanded.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)
    fetch('/api/files/tree')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`${r.status}`)))
      .then(d => { if (!cancelled) setItems(d.items ?? []) })
      .catch(e => { if (!cancelled) setError(String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [reloadKey])

  const tree = useMemo(() => buildTree(items), [items])
  const totalSize = items.reduce((s, it) => s + (it.size ?? 0), 0)

  function toggle(path: string) {
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  function renderNode(node: TreeNode, depth: number): React.ReactNode {
    const isFolder = node.children.size > 0 || (node.file && node.path.endsWith('/'))
    const isCollapsed = collapsed.has(node.path)
    const indent = { paddingLeft: `${depth * 14}px` }
    if (isFolder) {
      return (
        <div key={node.path}>
          <div className="files__row files__row--folder" style={indent}>
            <button
              className="files__chev"
              onClick={() => toggle(node.path)}
              title={isCollapsed ? 'Expand' : 'Collapse'}
            >{isCollapsed ? '▸' : '▾'}</button>
            <span className="files__icon files__icon--folder">📁</span>
            {node.file ? (
              <button
                className={`files__name files__name--clickable ${focusedId === node.file.entity_id ? 'is-current' : ''}`}
                onClick={() => onFocus(node.file!.entity_id)}
                title={`${node.file.type} · ${node.file.entity_id}`}
              >{node.name}/</button>
            ) : (
              <span className="files__name">{node.name}/</span>
            )}
            <span className="files__spacer" />
            <a
              className="files__action"
              href={folderDownloadUrl(node.path)}
              title="Download as .zip"
              download
              onClick={e => e.stopPropagation()}
            >⬇</a>
          </div>
          {!isCollapsed && (
            <div className="files__children">
              {Array.from(node.children.values())
                .sort((a, b) => a.name.localeCompare(b.name))
                .map(c => renderNode(c, depth + 1))}
            </div>
          )}
        </div>
      )
    }
    // Leaf file
    const f = node.file
    return (
      <div key={node.path} className={`files__row files__row--file ${focusedId === f?.entity_id ? 'is-current' : ''}`} style={indent}>
        <span className="files__chev files__chev--blank" />
        <span className="files__icon">{isImage(f) ? '🖼' : f?.type === 'note' ? '📝' : '📄'}</span>
        <button
          className="files__name files__name--clickable"
          onClick={() => f && onFocus(f.entity_id)}
          title={f ? `${f.type} · ${f.entity_id}` : ''}
        >{node.name}</button>
        <span className="files__size">{fmtSize(f?.size ?? null)}</span>
        {f && f.artifact_path && (
          <a
            className="files__action"
            href={downloadUrl(f)}
            title="Download"
            download
            onClick={e => e.stopPropagation()}
          >⬇</a>
        )}
      </div>
    )
  }

  return (
    <section className="tree__index tree__index--files">
      <div className="tree__index-head">
        <div className="tree__title-row">
          <span className="tree__tab-badge">
            <span className="files__icon">📁</span>
            Files
            <span className="tree__pill tree__pill--green">{items.length}</span>
          </span>
          <a
            className="files__head-action"
            href={folderDownloadUrl('')}
            title="Download the whole tree as .zip"
            download
          >⬇ all</a>
        </div>
        <div className="files__totals">
          {items.length} files · {fmtSize(totalSize)}
        </div>
      </div>
      <div className="files__body">
        {error && <div className="files__error">Couldn't load files: {error}</div>}
        {loading && !items.length && <div className="files__empty">Loading…</div>}
        {!loading && !items.length && !error && (
          <div className="files__empty">No artifacts yet — run an analysis to populate the tree.</div>
        )}
        {Array.from(tree.children.values())
          .sort((a, b) => a.name.localeCompare(b.name))
          .map(n => renderNode(n, 0))}
      </div>
    </section>
  )
}
