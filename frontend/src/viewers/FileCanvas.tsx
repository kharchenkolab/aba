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
import { useState } from 'react'
import type { FileNode, ViewersResponse } from './types'
import { VIEWERS, hasViewer } from './registry'
import { useViewerRegistry, dispatchResponse } from './dispatch'
import './FileCanvas.css'

interface Props {
  node: FileNode
  onFocus?: (id: string) => void
  onClose?: () => void
}

export default function FileCanvas({ node, onFocus, onClose }: Props) {
  // Client-side dispatch (no per-click round-trip). The registry is
  // fetched once at app start and cached; dispatch is a pure local
  // computation from the node's extension / entity-type / size.
  const registry = useViewerRegistry()
  const resp: ViewersResponse | null = registry ? dispatchResponse(node, registry) : null
  const [err] = useState<string | null>(null)

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
    return <NoViewerFallback node={node} resp={resp} />
  }
  const Component = VIEWERS[picked.component!]
  return <Component node={node} viewer={picked} onFocus={onFocus} onClose={onClose} />
}


/** Shown when no in-app canvas viewer applies for a file. Surfaces the
 *  download, any external launchers, and the AI summary / visualize
 *  fallbacks — so the user always has a way forward. */
function NoViewerFallback({ node, resp }: { node: FileNode; resp: ViewersResponse }) {
  const [summary, setSummary] = useState<string | null>(null)
  const [pending, setPending] = useState<'summary' | 'visualize' | null>(null)
  const [aiErr, setAiErr] = useState<string | null>(null)

  const externals = resp.viewers.filter(v => v.mode === 'external')
  const hasAiSummary = !!resp.viewers.find(v => v.id === 'ai-summary')
  const hasAiVisualize = !!resp.viewers.find(v => v.id === 'ai-visualize')

  async function askSummary() {
    setPending('summary'); setAiErr(null); setSummary(null)
    try {
      const r = await fetch('/api/files/ai-summary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(node.path ? { path: node.path } : { entity_id: node.entity_id }),
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d?.detail || `${r.status}`)
      setSummary(d.markdown as string)
    } catch (e) {
      setAiErr(String(e))
    } finally {
      setPending(null)
    }
  }

  return (
    <article className="viewer viewer--fallback">
      <header className="viewer__head">
        <span className="viewer__path">{node.path}</span>
      </header>
      <div className="viewer__body" style={{ padding: 18, maxWidth: 720 }}>
        <p style={{ color: 'var(--text-3)' }}>
          No in-app viewer for this file type yet.
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
          {resp.download_url && (
            <a className="viewer__action" href={resp.download_url} download>⬇ Download</a>
          )}
          {externals.map(v => (
            <span key={v.id} className="viewer__action viewer__action--disabled" title="External launcher not yet wired">
              {v.label} (external)
            </span>
          ))}
          {hasAiSummary && (
            <button className="viewer__action viewer__action--ai" onClick={askSummary} disabled={pending === 'summary'}>
              {pending === 'summary' ? '… asking Guide' : '✦ Ask Guide to summarize'}
            </button>
          )}
          {hasAiVisualize && (
            <span className="viewer__action viewer__action--disabled" title="AI visualize lands in V4">
              ✦ Ask Guide to visualize (soon)
            </span>
          )}
        </div>
        {aiErr && <div className="viewer__error" style={{ marginTop: 12 }}>{aiErr}</div>}
        {summary && (
          <div className="viewer__ai-summary">
            <div className="viewer__ai-summary-label">Guide says</div>
            <pre className="viewer__ai-summary-body">{summary}</pre>
          </div>
        )}
      </div>
    </article>
  )
}
