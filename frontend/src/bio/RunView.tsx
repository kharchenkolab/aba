/**
 * Run (analysis run) entity view — the recompute/branch unit (entity-model v3).
 * Reorganized around run state: a compact title + status/controls band, then a
 * state-driven main area where — once a run completes — the OUTPUTS are the
 * focus (succinct cells: counts + links, not every artifact). Running/failed
 * runs foreground the log; queued runs foreground the queue.
 */
import { useEffect, useMemo, useState } from 'react'
import type { Entity } from '../types'
import { EntityGlyph } from '../components/icons'
import ResultList, { type OutputItem } from '../components/ResultList'
import FileBrowser, { type TreeNode } from './FileBrowser'
import FileCanvas from '../viewers/FileCanvas'
import type { FileNode } from '../viewers/types'
import './RunView.css'

export interface RunMeta {
  executor?: string; status?: string; where?: string; queue?: string; scheduler_job_id?: string
  resources?: { cores?: number; mem?: string; walltime?: string }
  submitted_at?: string; started_at?: string; finished_at?: string
  command?: string; log_tail?: string; error?: string
  inputs?: { label: string; entity_id?: string }[]
  outputs?: OutputItem[]
  browse?: { label: string; href: string }
  bulk?: { count: number; note?: string; href?: string }
}
const STATUS_LABEL: Record<string, string> = {
  queued: 'Queued', running: 'Running', succeeded: 'Succeeded', failed: 'Failed', cancelled: 'Cancelled',
}
function rel(a?: string, bIso?: string): string {
  if (!a) return ''
  const b = bIso ? new Date(bIso).getTime() : Date.now()
  const s = Math.max(0, Math.round((b - new Date(a).getTime()) / 1000))
  if (s < 60) return `${s}s`
  const m = Math.round(s / 60); if (m < 60) return `${m}m`
  const h = Math.floor(m / 60); return `${h}h ${m % 60}m`
}
export default function RunView({ run, entities, onFocus, onChange, onAsk, onChatResult, onBrowseFiles }: {
  run: Entity; entities: Entity[]; onFocus: (id: string) => void; onChange: () => void
  onAsk?: (t: string) => void
  onChatResult?: (label: string, thumb?: string, annotation?: { image: string; note: string }) => void
  onBrowseFiles?: (path?: string) => void
}) {
  void entities
  const m = (run.metadata?.run ?? {}) as RunMeta
  const status = m.status || 'succeeded'
  const hpc = m.executor === 'remote-hpc'
  const active = status === 'running' || status === 'queued'
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(run.title)
  const [panel, setPanel] = useState<'command' | 'inputs' | 'log' | null>(
    status === 'failed' || status === 'running' ? 'log' : null)

  // The Run's full output directory, browsed with the shared FileBrowser —
  // nested folders (model/, figures/…) and every file type, with pin/discuss.
  const [runTree, setRunTree] = useState<TreeNode | null>(null)
  const [modalNode, setModalNode] = useState<TreeNode | null>(null)
  useEffect(() => {
    let cancelled = false
    fetch(`/api/runs/${encodeURIComponent(run.id)}/tree`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (!cancelled) setRunTree(d as TreeNode) })
      .catch(() => { if (!cancelled) setRunTree(null) })
    return () => { cancelled = true }
  }, [run.id])

  // Esc closes the file-preview modal.
  useEffect(() => {
    if (!modalNode) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setModalNode(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [modalNode])

  function fileHref(node: { artifact_path?: string | null; path: string }): string {
    const ap = node.artifact_path || ''
    return ap.startsWith('/artifacts/') || ap.startsWith('http')
      ? ap : `/api/files/content?path=${encodeURIComponent(node.path)}`
  }
  // View a file in a MODAL overlay (keeps the run in the middle column), the same
  // way plot thumbnails preview. The "Browse in Files tab" link is the escape
  // hatch for those who want the full middle-column viewer.
  function browseViewFile(node: FileNode) { setModalNode(node as TreeNode) }
  // path → pinned-result entity id, so a second click UNPINS (archives) it
  // instead of adding a duplicate to the thread.
  const [pinnedIds, setPinnedIds] = useState<Record<string, string>>({})
  async function pinFile(node: TreeNode, pinned: boolean) {
    if (pinned) {
      const ext = (node.name.split('.').pop() || '').toLowerCase()
      const kind = /^(png|jpe?g|gif|svg|webp)$/.test(ext) ? 'figure' : /^(csv|tsv)$/.test(ext) ? 'table' : 'file'
      const href = fileHref(node)
      try {
        const r = await fetch(`/api/runs/${encodeURIComponent(run.id)}/pin-output`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ kind, label: node.name, thumb: kind === 'figure' ? href : undefined, href }),
        })
        const ent = await r.json().catch(() => null)
        if (ent?.id) setPinnedIds(m => ({ ...m, [node.path]: ent.id }))
      } catch { /* leave unpinned on failure */ }
    } else {
      const id = pinnedIds[node.path]
      if (id) await fetch(`/api/entities/${encodeURIComponent(id)}`, { method: 'DELETE' }).catch(() => {})
      setPinnedIds(m => { const n = { ...m }; delete n[node.path]; return n })
    }
    onChange()
  }
  // Discuss: set the chat CONTEXT (prefill + focus composer) without sending —
  // mirrors the plot-thumbnail "Discuss" so the user types their own question.
  function discussFile(node: TreeNode) {
    const isImg = /\.(png|jpe?g|gif|svg|webp)$/i.test(node.name)
    onChatResult?.(node.name, isImg ? fileHref(node) : undefined)
  }

  const where = hpc ? `⛁ ${m.where || 'cluster'}${m.queue ? ` · ${m.queue}` : ''}` : '⚙ local'
  const elapsed = status === 'running' ? `running ${rel(m.started_at)}`
    : status === 'succeeded' || status === 'failed' ? rel(m.started_at || m.submitted_at, m.finished_at)
    : status === 'queued' ? `waiting ${rel(m.submitted_at)}` : ''

  async function patch(body: Record<string, unknown>) {
    await fetch(`/api/entities/${encodeURIComponent(run.id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).catch(() => {})
    onChange()
  }
  const saveTitle = () => { setEditing(false); if (title.trim() && title.trim() !== run.title) patch({ title: title.trim() }) }
  async function cancel() {
    await fetch(`/api/runs/${encodeURIComponent(run.id)}/cancel`, { method: 'POST' }).catch(() => {})
    onChange()
  }
  const toggle = (p: 'command' | 'inputs' | 'log') => setPanel(c => c === p ? null : p)

  const outputs = m.outputs ?? []
  // Hide superseded revisions from the Plots strip. When the agent
  // revises a figure (make_revision), both the original and the new
  // revision land in the run dir and the manifest lists both. We only
  // want the chain HEAD for each figure — the latest active version.
  // Map outputs to entities via artifact_id (= exec_id:kind:idx) and
  // drop any whose entity has a known active descendant via
  // metadata.revision_of. Outputs without artifact_id (legacy /
  // unpinned files) are always kept — we can't tell if they're stale.
  const supersededByDescendant = useMemo(() => {
    const s = new Set<string>()
    for (const e of entities) {
      if (e.status !== 'active') continue
      const ro = (e.metadata as { revision_of?: string } | undefined)?.revision_of
      if (ro) s.add(ro)
    }
    return s
  }, [entities])
  const entityByArtifactId = useMemo(() => {
    const m: Record<string, typeof entities[number]> = {}
    for (const e of entities) {
      if (e.exec_id && e.artifact_kind && e.artifact_idx != null) {
        m[`${e.exec_id}:${e.artifact_kind}:${e.artifact_idx}`] = e
      }
    }
    return m
  }, [entities])
  // Artifact_ids already wrapped in an active Result — drives the pin
  // icon flip in ResultList. Backend stamps Result.metadata.
  // primary_evidence_id on create (see pin_evidence). We walk Results,
  // resolve evidence → entity → artifact_id, and emit the matching keys.
  // Cross-thread Results don't show as pinned here (they belong to a
  // different conversation's pin state).
  const entityById = useMemo(() => {
    const m: Record<string, typeof entities[number]> = {}
    for (const e of entities) m[e.id] = e
    return m
  }, [entities])
  const pinnedArtifactIds = useMemo(() => {
    const s = new Set<string>()
    for (const e of entities) {
      if (e.type !== 'result' || e.status !== 'active') continue
      const eid = (e.metadata as { primary_evidence_id?: string } | null)?.primary_evidence_id
      if (!eid) continue
      const ev = entityById[eid]
      if (ev?.exec_id && ev.artifact_kind && ev.artifact_idx != null) {
        s.add(`${ev.exec_id}:${ev.artifact_kind}:${ev.artifact_idx}`)
      }
    }
    return s
  }, [entities, entityById])
  const isLatestOutput = (it: OutputItem): boolean => {
    if (!it.artifact_id) return true                 // unpinned/legacy → keep
    const ent = entityByArtifactId[it.artifact_id]
    if (!ent) return true                            // no entity yet → keep
    return !supersededByDescendant.has(ent.id)       // hide if it has a descendant
  }
  const plotOutputs = outputs
    .filter(o => o.kind === 'figure' || o.kind === 'view')
    .filter(isLatestOutput)
  // Pin/unpin toggle. The pin button reports whether the output is
  // currently pinned via `pinnedArtifactIds`; clicking flips it:
  //   - already pinned  → POST /api/entities/{evidence_id}/unpin
  //                       (archives the wrapping Result via unpin_evidence)
  //   - not pinned      → POST /api/artifacts/{exec}/{kind}/{idx}/pin
  //                       (idempotent, materializes + wraps in a Result)
  // The /pin-output fallback stays for run outputs that don't have an
  // artifact_id (truly orphan manifest entries) — those can't be
  // toggled, only added.
  const pinOutput = async (it: OutputItem) => {
    if (it.artifact_id) {
      const already = pinnedArtifactIds.has(it.artifact_id)
      if (already) {
        const ent = entityByArtifactId[it.artifact_id]
        if (ent) {
          await fetch(`/api/entities/${encodeURIComponent(ent.id)}/unpin`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
          }).catch(() => {})
          onChange()
          return
        }
      }
      const m = it.artifact_id.match(/^(.+):([^:]+):(\d+)$/)
      if (m) {
        const [, exec_id, kind, idxs] = m
        await fetch(
          `/api/artifacts/${encodeURIComponent(exec_id)}/${encodeURIComponent(kind)}/${idxs}/pin`,
          { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
        ).catch(() => {})
        onChange()
        return
      }
    }
    await fetch(`/api/runs/${encodeURIComponent(run.id)}/pin-output`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: it.kind, label: it.label, thumb: it.thumb, href: it.href, size: it.size }),
    }).catch(() => {})
    onChange()
  }
  const chatOutput = (it: OutputItem) =>
    onChatResult ? onChatResult(it.label, it.thumb) : onAsk?.(`Let's look at "${it.label}" from the run "${run.title}".`)
  const registerOutput = async (it: OutputItem) => {
    await fetch(`/api/runs/${encodeURIComponent(run.id)}/register-dataset`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: it.label, path: it.href, size: it.size }),
    }).catch(() => {})
    onChange()
  }

  const pill = (
    <span className={`run-pill run-pill--${status}`}>
      {active && status === 'running' && <span className="run-pill__dot" />}{STATUS_LABEL[status] ?? status}
    </span>
  )

  return (
    <div className="runview">
      {/* Title — status pill sits to its right */}
      <div className="runview__titlerow">
        {editing ? (
          <input className="runview__title-input" autoFocus value={title}
                 onChange={e => setTitle(e.target.value)} onBlur={saveTitle}
                 onKeyDown={e => { if (e.key === 'Enter') saveTitle(); if (e.key === 'Escape') { setTitle(run.title); setEditing(false) } }} />
        ) : (
          <h1 className="runview__title" onClick={() => setEditing(true)} title="Click to rename">{run.title}</h1>
        )}
        {pill}
      </div>

      {/* Status + controls band */}
      <div className="runview__band">
        <span className="runview__meta">{where}</span>
        {elapsed && <span className="runview__meta runview__meta--dim">{elapsed}</span>}
        {m.scheduler_job_id && <span className="runview__meta runview__meta--dim">job {m.scheduler_job_id}</span>}
        <span className="runview__spacer" />
        {active && <button className="runview__act runview__act--danger" onClick={cancel}>Cancel</button>}
        {!active && onAsk && <button className="runview__act" onClick={() => onAsk(`Re-run "${run.title}" as-is.`)}>Re-run</button>}
        {!active && onAsk && <button className="runview__act" onClick={() => onAsk(`Re-run "${run.title}" with a change: `)}>Re-run with changes…</button>}
        {onAsk && <button className="runview__act" onClick={() => onAsk(`Where does the run "${run.title}" stand — what it did and what the outputs show?`)}>Discuss</button>}
      </div>
      <div className="runview__toggles">
        {m.command && <button className={panel === 'command' ? 'is-on' : ''} onClick={() => toggle('command')}>Command</button>}
        {m.inputs && m.inputs.length > 0 && <button className={panel === 'inputs' ? 'is-on' : ''} onClick={() => toggle('inputs')}>Inputs</button>}
        {(m.log_tail || m.error) && <button className={panel === 'log' ? 'is-on' : ''} onClick={() => toggle('log')}>Log</button>}
      </div>
      {panel === 'command' && m.command && <pre className="runview__pre">{m.command}</pre>}
      {panel === 'inputs' && (
        <div className="runview__chips">
          {(m.inputs ?? []).map((inp, i) => (
            <button key={i} className="run-chip" disabled={!inp.entity_id} onClick={() => inp.entity_id && onFocus(inp.entity_id)}>
              <EntityGlyph name="dataset" size={12} />{inp.label}
            </button>
          ))}
        </div>
      )}
      {panel === 'log' && (m.error || m.log_tail) && (
        <pre className={`runview__pre ${m.error ? 'runview__pre--err' : ''}`}>{m.error ? m.error + '\n\n' : ''}{m.log_tail}</pre>
      )}

      {/* State-driven main */}
      {status === 'queued' && (
        <div className="runview__state runview__state--queued">
          <div className="runview__state-head">Queued on {m.where || 'the queue'}{m.queue ? ` · ${m.queue}` : ''}</div>
          {m.resources && <div className="runview__state-sub">
            {[m.resources.cores && `${m.resources.cores} cores`, m.resources.mem && `${m.resources.mem} mem`, m.resources.walltime && `${m.resources.walltime} walltime`].filter(Boolean).join(' · ')}
          </div>}
          <div className="runview__state-sub">Waiting {rel(m.submitted_at)} — outputs will appear here when it runs.</div>
        </div>
      )}
      {status === 'running' && outputs.length === 0 && (
        <div className="runview__state">
          <div className="runview__state-sub">Running on {m.where || 'local'} — outputs will appear here as they’re produced. (Log above is live.)</div>
        </div>
      )}
      {status === 'failed' && outputs.length === 0 && (
        <div className="runview__state runview__state--failed">
          <div className="runview__state-sub">This run failed before producing outputs. See the error above; Re-run when fixed.</div>
        </div>
      )}
      {/* PLOTS — glanceable figure thumbnails. The redundant table/file rows +
          browse/bulk are dropped; the FileBrowser below owns all files. */}
      {plotOutputs.length > 0 && (
        <section className="runview__outputs">
          <div className="runview__outputs-head">Plots</div>
          <ResultList items={plotOutputs} runId={run.id} onPin={pinOutput} onChat={chatOutput}
            onChatAnnotated={(it, annot) => onChatResult?.(it.label, undefined, annot)}
            onRegister={registerOutput}
            pinnedArtifactIds={pinnedArtifactIds} />
        </section>
      )}

      {/* Full output directory — nested folders + every file, browsable with the
          shared FileBrowser (sortable/resizable columns, pin/discuss per file).
          Files open in a modal here; "Browse in Files tab" frees the middle column. */}
      {runTree && (runTree.children?.length ?? 0) > 0 && (
        <section className="runview__browse">
          <div className="runview__outputs-head runview__browse-head">
            Output files
            {onBrowseFiles && (
              <button className="runview__browse-link"
                      onClick={() => onBrowseFiles(
                        runTree?.children?.find(c => c.name === 'output')?.path || runTree?.path || '')}
                      title="Open this run's output folder in the Files tab (viewer in the middle column)">
                Browse in Files tab →
              </button>
            )}
          </div>
          <FileBrowser
            root={runTree}
            variant="wide"
            focusedId=""
            onFocus={onFocus}
            onViewFile={browseViewFile}
            actions={{ onPin: pinFile, onDiscuss: discussFile }}
            emptyHint="No files in this run's output folder."
          />
        </section>
      )}

      {modalNode && (
        <div className="runview__modal" onClick={() => setModalNode(null)}>
          <div className="runview__modal-box" onClick={e => e.stopPropagation()}>
            <button className="runview__modal-close" onClick={() => setModalNode(null)} aria-label="Close (Esc)" title="Close (Esc)">×</button>
            <div className="runview__modal-body">
              <FileCanvas node={modalNode} onFocus={onFocus} onClose={() => setModalNode(null)} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
