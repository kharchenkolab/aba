/**
 * FilesView — nested project file tree (files.md §3.3, §6).
 *
 * Renders the multi-rooted virtual tree composed by the backend
 * (threads → runs/results/claims, runs → child files, results → member
 * files). Same canonical artifact may appear at multiple paths.
 * Click a file → focus its entity. Click a folder name → expand/collapse;
 * if the folder is backed by an entity, click the entity icon to focus it.
 * Each folder + file has a ⬇ that downloads via the path-based endpoint.
 * Each container shows a generated README inline.
 */
import { useEffect, useMemo, useState } from 'react'
import './FilesView.css'

type TreeNode = {
  kind: 'root' | 'folder' | 'file' | 'readme'
  name: string
  path: string
  children?: TreeNode[]
  entity_id?: string | null
  entity_type?: string | null
  title?: string | null
  artifact_path?: string | null
  size?: number | null
  mtime?: number | null
  pinned?: boolean
  status?: string | null
  content?: string                 // readme markdown
  container_kind?: string
  synthesized?: boolean
}

interface Props {
  focusedId: string
  onFocus: (id: string) => void
  reloadKey?: unknown
}

function fmtSize(n: number | null | undefined): string {
  if (n == null) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`
}

function isImage(node: TreeNode): boolean {
  const p = node.artifact_path
  return !!p && /\.(png|jpe?g|gif|svg|webp)$/i.test(p)
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
  // Backend path-based zip download for any container; entity download
  // for leaves with an artifact (preserves the existing route).
  if (node.kind === 'file' && node.entity_id && node.artifact_path) {
    return `/api/entities/${encodeURIComponent(node.entity_id)}/download`
  }
  return `/api/files/download?path=${encodeURIComponent(node.path)}`
}

export default function FilesView({ focusedId, onFocus, reloadKey }: Props) {
  const [root, setRoot] = useState<TreeNode | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [openReadme, setOpenReadme] = useState<Set<string>>(new Set())
  const [materializeMsg, setMaterializeMsg] = useState<string | null>(null)

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

  // Aggregate stats from the tree.
  const stats = useMemo(() => {
    let files = 0, bytes = 0
    function walk(n: TreeNode) {
      if (n.kind === 'file' && n.artifact_path) { files += 1; bytes += n.size ?? 0 }
      for (const c of n.children ?? []) walk(c)
    }
    if (root) walk(root)
    return { files, bytes }
  }, [root])

  function toggleFolder(path: string) {
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path); else next.add(path)
      return next
    })
  }

  function toggleReadme(path: string) {
    setOpenReadme(prev => {
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

  function renderNode(node: TreeNode, depth: number): React.ReactNode {
    const indent = { paddingLeft: `${depth * 12}px` }
    if (node.kind === 'readme') {
      const isOpen = openReadme.has(node.path)
      return (
        <div key={node.path} className="files__readme" style={indent}>
          <div className="files__row files__row--readme">
            <button className="files__chev" onClick={() => toggleReadme(node.path)}>{isOpen ? '▾' : '▸'}</button>
            <span className="files__icon">{leafIcon(node)}</span>
            <span className="files__name files__name--readme">README.md</span>
            <span className="files__size">{fmtSize(node.size ?? null)}</span>
          </div>
          {isOpen && node.content && (
            <pre className="files__readme-body">{node.content}</pre>
          )}
        </div>
      )
    }
    if (node.kind === 'file') {
      return (
        <div
          key={node.path}
          className={`files__row files__row--file ${focusedId === node.entity_id ? 'is-current' : ''}`}
          style={indent}
          title={node.path}
        >
          <span className="files__chev files__chev--blank" />
          <span className="files__icon">{leafIcon(node)}</span>
          <button
            className="files__name files__name--clickable"
            onClick={() => node.entity_id && onFocus(node.entity_id)}
            title={`${node.entity_type ?? ''} · ${node.entity_id ?? ''}`}
          >{node.name}</button>
          <span className="files__size">{fmtSize(node.size ?? null)}</span>
          {(node.artifact_path || node.synthesized) && (
            <a
              className="files__action"
              href={downloadUrl(node)}
              title="Download"
              download
              onClick={e => e.stopPropagation()}
            >⬇</a>
          )}
        </div>
      )
    }
    // Folder (or root)
    const isCollapsed = collapsed.has(node.path)
    const hasEntity = !!node.entity_id
    return (
      <div key={node.path || '__root__'}>
        {node.kind !== 'root' && (
          <div className="files__row files__row--folder" style={indent}>
            <button className="files__chev" onClick={() => toggleFolder(node.path)}>
              {isCollapsed ? '▸' : '▾'}
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
            <span className="files__spacer" />
            <a
              className="files__action"
              href={downloadUrl(node)}
              title="Download folder as .zip"
              download
              onClick={e => e.stopPropagation()}
            >⬇</a>
          </div>
        )}
        {!isCollapsed && node.children && (
          <div className="files__children">
            {node.children.map(c => renderNode(c, node.kind === 'root' ? 0 : depth + 1))}
          </div>
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
            <span className="tree__pill tree__pill--green">{stats.files}</span>
          </span>
          <a
            className="files__head-action"
            href="/api/files/download"
            title="Download the whole tree as .zip"
            download
          >⬇ zip</a>
          <button
            className="files__head-action"
            onClick={materialize}
            title="Materialize on disk (symlinks → canonical artifacts)"
          >▤ folder</button>
        </div>
        <div className="files__totals">
          {stats.files} files · {fmtSize(stats.bytes)}
        </div>
        {materializeMsg && <div className="files__notice">{materializeMsg}</div>}
      </div>
      <div className="files__body">
        {error && <div className="files__error">Couldn't load files: {error}</div>}
        {loading && !root && <div className="files__empty">Loading…</div>}
        {!loading && root && (root.children?.length ?? 0) === 0 && (
          <div className="files__empty">No artifacts yet — run an analysis to populate the tree.</div>
        )}
        {root && renderNode(root, 0)}
      </div>
    </section>
  )
}
