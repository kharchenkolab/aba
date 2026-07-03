/**
 * Per-entity-type focus-view registry — the frontend side of arch3.md
 * Phase 9 (modularity_audit.md Tier 3 #8). FocusCanvas used to carry a
 * 100-line switch(entity.type) dispatching bio knowledge from a
 * "platform shell" component; this module owns that dispatch instead.
 *
 * Adding a new bio entity type now means:
 *   1. write content/bio/entity_types/<type>.yaml
 *   2. register a view component here via `register_focus_view`
 *
 * The platform shell (FocusCanvas) calls `focus_view_for(type)` and
 * renders whatever comes back — it knows nothing about figures vs
 * datasets vs claims. Phase 4.6 built the *metadata* registry
 * (entityTypes.ts); this is the *view-component* registry that pairs
 * with it. Together they're the frontend mirror of the backend YAML
 * declarations.
 *
 * Components that already exist as standalone files (RunView,
 * ResultView, ClaimView, AnnotatedFigure) are wrapped via tiny
 * adapters that translate the unified FocusViewProps into each
 * component's existing prop signature — no churn outside this file.
 * The smaller per-type body fragments (figure, dataset, note, table,
 * narrative, finding) used to live inline in FocusCanvas and now
 * live here as named components, alongside the helper sub-components
 * they needed (DatasetDescription, DatasetFiles, NarrativeBody,
 * FindingBody, PreviewTable).
 */
import { useEffect, useRef, useState } from 'react'
import type { ComponentType } from 'react'
import type { Entity } from '../types'
import RunView from './RunView'
import ResultView from './ResultView'
import ClaimView from './ClaimView'
import FileBrowser, { type TreeNode } from './FileBrowser'
import FileCanvas from '../viewers/FileCanvas'
import type { FileNode } from '../viewers/types'
import ExternalViewerActions from '../components/ExternalViewerActions'
import UploadDrop from '../platform/UploadDrop'
// RevisionChevrons (floating overlay) is deprecated 2026-06-07. The
// new RevisionStrip lives in ResultView's MemberPanel (below the figure,
// not over it). Direct figure/table focus surfaces lean on the
// FocusCanvas header's history clock + SplitButton — no in-body chevrons.


// ---------- Registry API ----------

/** Unified prop shape every focus view receives. Views destructure
 *  what they need; the shell passes everything that any view might
 *  use, regardless of whether the focused entity needs it. */
export interface FocusViewProps {
  entity: Entity
  /** Sibling entities in the project, used for cross-entity lookups
   *  (e.g. a Result rendering its members, a Claim rendering its
   *  evidence list). */
  entities: Entity[]
  onFocus: (id: string) => void
  onChange: () => void
  compact?: boolean
  onAsk?: (text: string) => void
  onChatResult?: (label: string, thumb?: string,
                  annotation?: { image: string; note: string },
                  action?: 'chat' | 'revision' | 'revision-supersede' | 'reproduce',
                  entityId?: string) => void
  /** Per-view annotation attach (highlight tool): some views (Result)
   *  carry a freehand-highlight surface inside the body. Captured strokes
   *  arrive here and propagate to the composer via App.tsx's
   *  attachAnnotation. Mirrors FocusCanvas's `onAnnotate` for figures. */
  onAnnotate?: (a: { image: string; note: string }) => void
  /** Bumped to clear any drawn marks (focus change, attach commit). */
  annotClear?: number
  /** Highlight-mode lifted from App.tsx so the canvas-actions row's
   *  ✏️ button drives ResultView's per-MemberPanel hover surfaces. */
  highlighting?: boolean
  onHighlightingChange?: (on: boolean) => void
  onBrowseFiles?: (path?: string) => void
  projectId?: string
}

const _registry = new Map<string, ComponentType<FocusViewProps>>()

/** Register a view component for the given entity type. Last
 *  registration wins (lets a host app override bio defaults). */
export function register_focus_view(type: string, view: ComponentType<FocusViewProps>): void {
  _registry.set(type, view)
}

/** Look up the registered view for an entity type. Returns null
 *  when no view is registered — the shell falls back to a generic
 *  placeholder (which is also how unregistered types render). */
export function focus_view_for(type: string): ComponentType<FocusViewProps> | null {
  return _registry.get(type) ?? null
}

/** All registered type keys. Lets the shell render a debug listing
 *  ("9 view types registered") or assert coverage in tests. */
export function registered_focus_view_types(): string[] {
  return [...(_registry.keys() as Iterable<string>)]
}


// ---------- Helper sub-components (lifted from FocusCanvas) ----------

interface TablePreview {
  kind: 'table'
  columns: string[]
  rows: unknown[][]
  total_rows: number
  shown: number
}
interface NonePreview { kind: 'none' }
interface ErrorPreview { kind: 'error'; error: string }
type Preview = TablePreview | NonePreview | ErrorPreview


/** Paginated CSV/TSV preview. Used by DatasetView (header + table)
 *  and TableView (table-as-entity). Fetches a window per page via
 *  /api/entities/{id}/preview?limit=&offset=. */
function PreviewTable({ entityId, pageSize = 25 }: { entityId: string; pageSize?: number }) {
  const [page, setPage] = useState(0)
  const [data, setData] = useState<TablePreview | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => { setPage(0) }, [entityId])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch(`/api/entities/${encodeURIComponent(entityId)}/preview?limit=${pageSize}&offset=${page * pageSize}`)
      .then(r => (r.ok ? r.json() : Promise.reject(r)))
      .then((p: Preview) => { if (!cancelled && p.kind === 'table') setData(p) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [entityId, page, pageSize])

  if (!data) return <p className="focus__placeholder">Loading table…</p>

  const total = data.total_rows
  const start = page * pageSize
  const end = start + data.rows.length
  const pages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <div className="focus__preview-wrap">
      <table className="focus__preview-table">
        <thead><tr>{data.columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {data.rows.map((row, i) => (
            <tr key={i}>{row.map((v, j) => <td key={j}>{v == null ? <em>·</em> : String(v)}</td>)}</tr>
          ))}
        </tbody>
      </table>
      <div className="focus__preview-foot">
        <span>{total === 0 ? 'no rows' : `${start + 1}–${end} of ${total} rows`}</span>
        {pages > 1 && (
          <span className="focus__pager">
            <button className="focus__pager-btn" disabled={page === 0 || loading}
                    onClick={() => setPage(p => Math.max(0, p - 1))} title="Previous page">‹</button>
            <span className="focus__pager-label">{page + 1} / {pages}</span>
            <button className="focus__pager-btn" disabled={page >= pages - 1 || loading}
                    onClick={() => setPage(p => p + 1)} title="Next page">›</button>
          </span>
        )}
      </div>
    </div>
  )
}


/** Inline auto-grow description for a dataset; saves on blur. */
function DatasetDescription({ entity, onChange }: { entity: Entity; onChange: () => void }) {
  const [val, setVal] = useState(entity.notes ?? '')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setVal(entity.notes ?? '') }, [entity.id])
  const save = () => {
    if ((val ?? '') === (entity.notes ?? '')) return
    fetch(`/api/entities/${encodeURIComponent(entity.id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notes: val }),
    }).then(() => onChange()).catch(() => {})
  }
  const ref = useRef<HTMLTextAreaElement>(null)
  const grow = () => { const t = ref.current; if (t) { t.style.height = 'auto'; t.style.height = `${t.scrollHeight}px` } }
  useEffect(grow, [val])
  return (
    <textarea ref={ref} className="focus__dataset-desc" value={val} rows={1}
      placeholder="Add a description… (what this dataset is, where it came from)"
      onChange={e => setVal(e.target.value)} onBlur={save} />
  )
}


/** Browse a dataset's directory contents with the shared FileBrowser.
 *  Trusts the tree endpoint's is_directory flag (disk-truth) instead
 *  of metadata.layout — older datasets and agent-registered ones often
 *  lack the flag even when the artifact_path IS a directory. */
function DatasetFiles({ entity, onFocus, onChatResult, onChange, projectId }: {
  entity: Entity
  onFocus: (id: string) => void
  onChatResult?: (label: string, thumb?: string,
                  annotation?: { image: string; note: string },
                  action?: 'chat' | 'revision' | 'revision-supersede' | 'reproduce',
                  entityId?: string) => void
  onChange: () => void
  projectId?: string
}) {
  const [tree, setTree] = useState<TreeNode | null>(null)
  const [modalNode, setModalNode] = useState<TreeNode | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [treeNonce, setTreeNonce] = useState(0)
  useEffect(() => {
    let cancelled = false
    fetch(`/api/datasets/${encodeURIComponent(entity.id)}/tree`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (!cancelled) setTree(d as TreeNode) })
      .catch(() => { if (!cancelled) setTree(null) })
    return () => { cancelled = true }
  }, [entity.id, treeNonce])
  useEffect(() => {
    if (!modalNode) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setModalNode(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [modalNode])

  const treeFlag = (tree as { is_directory?: boolean } | null)?.is_directory
  const isDirectoryDataset = treeFlag ?? (entity.metadata?.layout === 'directory')
  const empty = !tree || (tree.children?.length ?? 0) === 0
  if (empty && !isDirectoryDataset) return null

  const fileHref = (n: FileNode) => {
    const ap = n.artifact_path || ''
    return ap.startsWith('/artifacts/') || ap.startsWith('http') ? ap : `/api/files/content?path=${encodeURIComponent(n.path)}`
  }
  const discuss = (n: TreeNode) => {
    const img = /\.(png|jpe?g|gif|svg|webp)$/i.test(n.name)
    onChatResult?.(n.name, img ? fileHref(n) : undefined)
  }
  return (
    <section className="focus__dataset-files">
      <div className="focus__dataset-files-head">
        <span>Files</span>
        {isDirectoryDataset && (
          <button className="focus__add-files-btn"
                  onClick={() => setUploadOpen(true)}
                  title="Drop more files into this dataset">+ Add files</button>
        )}
      </div>
      {empty ? (
        <div className="focus__dataset-empty">
          This dataset has no files yet. Click <strong>+ Add files</strong> to drop in a file or folder.
        </div>
      ) : (
        <FileBrowser root={tree!} variant="wide" focusedId="" onFocus={onFocus}
          onViewFile={n => setModalNode(n as TreeNode)}
          actions={onChatResult ? { onDiscuss: discuss } : undefined} />
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
      {uploadOpen && (
        <UploadDrop
          appendTo={{ id: entity.id, title: entity.title }}
          projectId={projectId}
          onClose={() => setUploadOpen(false)}
          onUploaded={() => { onChange(); setTreeNonce(n => n + 1) }}
        />
      )}
    </section>
  )
}


function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`
}


// ---------- Per-entity-type view components ----------


function FigureView({ entity }: FocusViewProps) {
  if (!entity.artifact_path) {
    return <p className="focus__placeholder">No artifact attached.</p>
  }
  return <img className="focus__figure" src={entity.artifact_path} alt={entity.title} />
}


function DatasetView({ entity, onFocus, onChange, onChatResult, projectId }: FocusViewProps) {
  const [preview, setPreview] = useState<Preview | null>(null)
  useEffect(() => {
    setPreview(null)
    let cancelled = false
    fetch(`/api/entities/${encodeURIComponent(entity.id)}/preview?limit=10`)
      .then(r => (r.ok ? r.json() : Promise.reject(r)))
      .then((p: Preview) => { if (!cancelled) setPreview(p) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [entity.id])

  return (
    <div className="focus__dataset">
      <DatasetDescription entity={entity} onChange={onChange} />
      <div className="focus__rows">
        <div className="focus__row">
          <span className="focus__row-label">file</span>
          <code className="focus__row-val">{entity.artifact_path ?? '—'}</code>
        </div>
        {entity.metadata?.source ? (
          <div className="focus__row">
            <span className="focus__row-label">source</span>
            <span className="focus__row-val">{String(entity.metadata.source)}</span>
          </div>
        ) : null}
        {entity.metadata?.organism ? (
          <div className="focus__row">
            <span className="focus__row-label">organism</span>
            <span className="focus__row-val">{String(entity.metadata.organism)}</span>
          </div>
        ) : null}
        {entity.metadata?.size_bytes != null && (
          <div className="focus__row">
            <span className="focus__row-label">size</span>
            <span className="focus__row-val">{formatBytes(Number(entity.metadata.size_bytes))}</span>
          </div>
        )}
        {preview?.kind === 'table' && (
          <div className="focus__row">
            <span className="focus__row-label">rows × cols</span>
            <span className="focus__row-val">
              {preview.total_rows} × {preview.columns.length}
            </span>
          </div>
        )}
      </div>
      {preview?.kind === 'table' && <PreviewTable entityId={entity.id} pageSize={15} />}
      {preview?.kind === 'error' && (
        <div className="focus__placeholder">preview error: {preview.error}</div>
      )}
      <ExternalViewerActions entity={entity} />
      <DatasetFiles entity={entity} onFocus={onFocus} onChatResult={onChatResult}
                    onChange={onChange} projectId={projectId} />
    </div>
  )
}


function NoteView({ entity }: FocusViewProps) {
  return (
    <div className="focus__abstract">
      <p className="focus__interpretation">{(entity.metadata?.text as string) ?? entity.notes ?? entity.title}</p>
      <p className="focus__placeholder">Kept from the conversation.</p>
    </div>
  )
}


function TableView({ entity }: FocusViewProps) {
  return <PreviewTable entityId={entity.id} pageSize={25} />
}


function NarrativeView({ entity, onChange }: FocusViewProps) {
  const [text, setText] = useState((entity.metadata?.text as string) ?? '')
  const [dirty, setDirty] = useState(false)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setText((entity.metadata?.text as string) ?? ''); setDirty(false) }, [entity.id])

  async function save() {
    const meta = { ...(entity.metadata ?? {}), text }
    await fetch(`/api/entities/${encodeURIComponent(entity.id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notes: text.slice(0, 200), metadata: meta }),
    })
    setDirty(false); onChange()
  }
  return (
    <div className="focus__narrative">
      <textarea
        className="focus__narrative-text"
        value={text}
        placeholder="Write this manuscript section. Reference claims and findings as you build the argument…"
        onChange={e => { setText(e.target.value); setDirty(true) }}
        rows={10}
      />
      <div className="focus__narrative-bar">
        {dirty ? <button className="focus__promote" onClick={save}>Save</button>
               : <span className="focus__placeholder">Saved. The Stylist reviews on focus.</span>}
      </div>
    </div>
  )
}


const MATURITY = ['draft', 'candidate', 'checked', 'manuscript'] as const

interface Caveat { text: string; source?: string }


function FindingView({ entity, entities, onFocus, onChange }: FocusViewProps) {
  const finding = entity
  const meta = finding.metadata ?? {}
  const [picking, setPicking] = useState(false)
  const [summary, setSummary] = useState((meta.summary as string) ?? (meta.text as string) ?? '')
  const [editingSummary, setEditingSummary] = useState(false)
  const [newCaveat, setNewCaveat] = useState('')

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    setSummary((finding.metadata?.summary as string) ?? (finding.metadata?.text as string) ?? '')
  }, [finding.id])

  const status = (meta.maturity as string) ?? 'candidate'
  const caveats: Caveat[] = (meta.caveats as Caveat[]) ?? []
  const evIds = Array.from(new Set([
    ...((meta.evidence as string[]) ?? []),
    ...((meta.supporting_results as string[]) ?? []),
  ]))
  const evidenceEnts = evIds.map(id => entities.find(x => x.id === id)).filter((x): x is Entity => !!x)
  const candidates = entities.filter(
    e => e.type === 'result' && !evIds.includes(e.id) && e.status !== 'archived',
  )

  async function patch(body: Record<string, unknown>) {
    await fetch(`/api/findings/${encodeURIComponent(finding.id)}/fields`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    onChange()
  }
  async function addResult(resultId: string) {
    await fetch(`/api/findings/${encodeURIComponent(finding.id)}/add-result`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ result_id: resultId }),
    })
    setPicking(false); onChange()
  }
  async function removeResult(resultId: string) {
    await fetch(`/api/findings/${encodeURIComponent(finding.id)}/remove-result`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ result_id: resultId }),
    })
    onChange()
  }
  function addCaveat() {
    const t = newCaveat.trim()
    if (!t) return
    patch({ caveats: [...caveats, { text: t, source: 'user' }] })
    setNewCaveat('')
  }
  function removeCaveat(i: number) { patch({ caveats: caveats.filter((_, j) => j !== i) }) }

  return (
    <div className="fv">
      <div className="fv-ladder">
        {MATURITY.map((m, i) => {
          const done = MATURITY.indexOf(status as typeof MATURITY[number]) > i
          const cur = status === m
          return (
            <button key={m} className={`fv-step ${done ? 'is-done' : ''} ${cur ? 'is-current' : ''}`}
                    onClick={() => patch({ status: m })} title={`Mark ${m}`}>
              <span className="fv-step__dot" />{m}
            </button>
          )
        })}
      </div>

      <div className="fv-section-label">Summary <button className="fv-edit" onClick={() => setEditingSummary(v => !v)}>{editingSummary ? 'done' : 'edit'}</button></div>
      {editingSummary ? (
        <textarea className="fv-summary-edit" value={summary} autoFocus rows={4}
          onChange={e => setSummary(e.target.value)}
          onBlur={() => { setEditingSummary(false); if (summary !== (meta.summary ?? meta.text)) patch({ summary }) }} />
      ) : (
        <p className="fv-summary" onClick={() => setEditingSummary(true)}>{summary || <em className="focus__placeholder">Click to add a summary…</em>}</p>
      )}

      <div className="fv-section-label">Evidence ({evidenceEnts.length})
        <button className="fv-edit" onClick={() => setPicking(v => !v)} disabled={candidates.length === 0}>+ add</button>
      </div>
      {evidenceEnts.length === 0 && <div className="focus__placeholder">No evidence linked yet.</div>}
      {evidenceEnts.map(s => (
        <div key={s.id} className="focus__chain-row-wrap">
          <button className="focus__chain-row" onClick={() => onFocus(s.id)} type="button">
            <span className={`focus__type focus__type--${s.type}`}>{s.type}</span>
            <span className="focus__chain-title">{s.title}</span>
            <span className="focus__chain-arrow">↗</span>
          </button>
          <button className="focus__chain-remove" onClick={() => removeResult(s.id)} title="Remove from finding">×</button>
        </div>
      ))}
      {picking && (
        <div className="focus__picker">
          <div className="focus__picker-head">Add a result</div>
          {candidates.map(c => (
            <button key={c.id} className="focus__picker-row" onClick={() => addResult(c.id)}>
              <span className="focus__type focus__type--result">result</span>{c.title}
            </button>
          ))}
        </div>
      )}

      <div className="fv-section-label">Caveats ({caveats.length})</div>
      {caveats.map((c, i) => (
        <div key={i} className="fv-caveat">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
          <span className="fv-caveat__text">{c.text}</span>
          {c.source && <span className="fv-caveat__src">{c.source}</span>}
          <button className="fv-caveat__x" onClick={() => removeCaveat(i)} title="Remove caveat">×</button>
        </div>
      ))}
      <div className="fv-caveat-add">
        <input value={newCaveat} placeholder="Add a caveat…" onChange={e => setNewCaveat(e.target.value)}
               onKeyDown={e => { if (e.key === 'Enter') addCaveat() }} />
        <button onClick={addCaveat} disabled={!newCaveat.trim()}>Add</button>
      </div>
    </div>
  )
}


// --- Adapters for existing components ---
// RunView / ResultView / ClaimView already exist with their own prop
// signatures; these tiny wrappers translate the unified FocusViewProps
// into the args each component expects. Keeps the call-side of those
// components unchanged.

function RunViewAdapter({ entity, entities, onFocus, onChange, onAsk, onChatResult, onBrowseFiles }: FocusViewProps) {
  return <RunView run={entity} entities={entities} onFocus={onFocus} onChange={onChange}
                  onAsk={onAsk} onChatResult={onChatResult} onBrowseFiles={onBrowseFiles} />
}

function ResultViewAdapter({ entity, entities, onFocus, onChange, onAsk, onChatResult, onAnnotate, annotClear, highlighting, onHighlightingChange }: FocusViewProps) {
  return <ResultView result={entity} entities={entities} onFocus={onFocus} onChange={onChange}
                     onAsk={onAsk} onChatResult={onChatResult}
                     onAnnotate={onAnnotate} annotClear={annotClear}
                     highlighting={highlighting} onHighlightingChange={onHighlightingChange} />
}

function ClaimViewAdapter({ entity, entities, onFocus, onChange, compact }: FocusViewProps) {
  return <ClaimView claim={entity} entities={entities} onFocus={onFocus} onChange={onChange} compact={compact} />
}


// ---------- Registration ----------
// Mirror of backend/content/bio/entity_types/*.yaml. Each leaf/run/
// result/claim/finding/narrative gets a view; hidden types (capability,
// reference) and singletons (workspace, plan, thread) don't.

register_focus_view('figure',    FigureView)
register_focus_view('dataset',   DatasetView)
register_focus_view('analysis',  RunViewAdapter)
register_focus_view('result',    ResultViewAdapter)
register_focus_view('claim',     ClaimViewAdapter)
register_focus_view('finding',   FindingView)
register_focus_view('note',      NoteView)
register_focus_view('table',     TableView)
register_focus_view('narrative', NarrativeView)
