/**
 * FileCanvas — the dispatcher for non-entity file viewing in the
 * central column. Takes a tree node, asks the backend which viewers
 * apply, picks the highest-priority canvas-mode component we know
 * about, and renders it.
 *
 * For entity-backed files (figure, claim, result, …) the file tree
 * routes clicks through onFocus(entity_id) — that's still handled by
 * FocusCanvas. FileCanvas handles synthesized files (READMEs,
 * generated code/text, AI-summary results, etc.) and any plain
 * artifact that's not entity-backed.
 */
import { useEffect, useState } from 'react'
import type { FileNode, ViewersResponse } from './types'
import { VIEWERS, hasViewer } from './registry'

interface Props {
  node: FileNode
  onFocus?: (id: string) => void
  onClose?: () => void
}

export default function FileCanvas({ node, onFocus, onClose }: Props) {
  const [resp, setResp] = useState<ViewersResponse | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setResp(null); setErr(null)
    // Resolve the viewer set. For synthesized files we already have
    // enough information locally to pick a viewer; the round-trip
    // keeps the dispatch consistent and gives us alternates for the
    // future right-click menu.
    const q = node.entity_id
      ? `entity_id=${encodeURIComponent(node.entity_id)}`
      : `path=${encodeURIComponent(node.path)}`
    fetch(`/api/viewers/for?${q}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`${r.status}`)))
      .then(d => { if (!cancelled) setResp(d as ViewersResponse) })
      .catch(e => { if (!cancelled) setErr(String(e)) })
    return () => { cancelled = true }
  }, [node.path, node.entity_id])

  // Pick the highest-priority canvas-mode viewer whose component we
  // actually have in the frontend registry.
  const picked = resp?.viewers.find(
    v => v.mode === 'canvas' && hasViewer(v.component),
  ) ?? null

  if (err) {
    return <div className="viewer__error" style={{ padding: 16 }}>Viewer lookup failed: {err}</div>
  }
  if (!resp) {
    return <div className="viewer__empty" style={{ padding: 16 }}>Loading viewer…</div>
  }
  if (!picked) {
    // No canvas-mode viewer applies — fall back to a download link.
    return (
      <div className="viewer__empty" style={{ padding: 16 }}>
        <p>No in-app viewer for <code>{node.path}</code>.</p>
        {resp.download_url && (
          <p><a href={resp.download_url} download>⬇ Download the file</a></p>
        )}
      </div>
    )
  }
  const Component = VIEWERS[picked.component!]
  return <Component node={node} viewer={picked} onFocus={onFocus} onClose={onClose} />
}
