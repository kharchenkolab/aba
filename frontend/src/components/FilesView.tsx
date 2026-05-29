/**
 * FilesView — the Files left-rail tab. A thin host around the shared
 * <FileBrowser> (which owns all tree/list rendering, search, sort, viewer
 * dispatch): FilesView fetches the project tree, owns reload, and supplies the
 * rail-specific chrome (Files badge, download-all, materialize) + the
 * promote-to-dataset gesture. The same <FileBrowser> renders a Run's output
 * subtree in the Run view (wide variant).
 */
import { useEffect, useState } from 'react'
import './FilesView.css'
import type { FileNode } from '../viewers/types'
import FileBrowser, { type TreeNode } from './FileBrowser'

interface Props {
  focusedId: string
  onFocus: (id: string) => void
  onViewFile?: (node: FileNode) => void
  reloadKey?: unknown
  /** Deep-link: navigate the browser to this folder path (e.g. from a Run's
   *  "Browse in Files tab"). `targetNonce` makes repeat requests re-fire. */
  targetPath?: string
  targetNonce?: number
}

function countFiles(n: TreeNode | null): number {
  if (!n) return 0
  let c = n.kind === 'file' && n.artifact_path ? 1 : 0
  for (const k of n.children ?? []) c += countFiles(k)
  return c
}

export default function FilesView({ focusedId, onFocus, onViewFile, reloadKey, targetPath, targetNonce }: Props) {
  const [root, setRoot] = useState<TreeNode | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [bump, setBump] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)
    fetch('/api/files/tree')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`${r.status}`)))
      .then(d => { if (!cancelled) setRoot(d as TreeNode) })
      .catch(e => { if (!cancelled) setError(String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [reloadKey, bump])

  async function materialize() {
    setNotice('Building folder…')
    try {
      const pid = await (await fetch('/api/projects/current')).json().then(d => d.current)
      if (!pid) { setNotice('No active project.'); return }
      const r = await fetch(`/api/projects/${encodeURIComponent(pid)}/materialize?clean=true`, { method: 'POST' })
      const d = await r.json()
      setNotice(`Built ${d.out_dir}: ${d.linked ?? 0} linked, ${d.copied ?? 0} copied${d.missing ? `, ${d.missing} missing` : ''}.`)
    } catch (e) {
      setNotice(`Failed: ${String(e)}`)
    }
  }

  // Promote an unregistered working/scratch file into a curated Dataset entity.
  async function promote(node: TreeNode) {
    setNotice(`Promoting ${node.name}…`)
    try {
      const r = await fetch(
        `/api/files/promote?path=${encodeURIComponent(node.path)}&title=${encodeURIComponent(node.name)}`,
        { method: 'POST' },
      )
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setNotice(`Promote failed: ${d.detail || r.status}`); return }
      setNotice(`Promoted ${node.name} → dataset.`)
      setBump(b => b + 1)
      if (d.dataset_id) onFocus(d.dataset_id)
    } catch (e) {
      setNotice(`Promote failed: ${String(e)}`)
    }
  }

  const fileCount = countFiles(root)
  const titleSlot = (
    <span className="tree__tab-badge">
      <FolderGlyph />
      Files
      <span className="tree__pill tree__pill--green">{fileCount}</span>
    </span>
  )
  const actionsSlot = (
    <>
      <a className="files__icon-btn" href="/api/files/download" title="Download the whole tree as .zip" download aria-label="Download all"><DownloadGlyph /></a>
      <button className="files__icon-btn" onClick={materialize} title="Materialize on disk (symlinks → canonical artifacts)" aria-label="Materialize folder"><KebabGlyph /></button>
    </>
  )

  return (
    <FileBrowser
      root={root}
      focusedId={focusedId}
      onFocus={onFocus}
      onViewFile={onViewFile}
      variant="rail"
      actions={{ onPromote: promote }}
      loading={loading}
      error={error}
      emptyHint="No artifacts yet — run an analysis to populate the tree."
      titleSlot={titleSlot}
      actionsSlot={actionsSlot}
      notice={notice && <div className="files__notice">{notice}</div>}
      targetPath={targetPath}
      targetNonce={targetNonce}
    />
  )
}

function FolderGlyph() {
  return <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M2 4.5a1 1 0 0 1 1-1h3.5l1.5 1.5H13a1 1 0 0 1 1 1V12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1Z" /></svg>
}
function DownloadGlyph() {
  return <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M8 2.5v8" /><path d="M4.5 7L8 10.5 11.5 7" /><path d="M3 13.5h10" /></svg>
}
function KebabGlyph() {
  return <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="3.5" r="1.2" /><circle cx="8" cy="8" r="1.2" /><circle cx="8" cy="12.5" r="1.2" /></svg>
}
