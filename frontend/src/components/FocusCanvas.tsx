import { useEffect, useRef, useState } from 'react'
import type { Entity } from '../types'
import PromoteDialog from './PromoteDialog'
import AnnotatedFigure from './AnnotatedFigure'
import ClaimView from './ClaimView'
import RunView from './RunView'
import ResultView from './ResultView'
import ThreadHeader from './ThreadHeader'
import FileBrowser, { type TreeNode } from './FileBrowser'
import FileCanvas from '../viewers/FileCanvas'
import type { FileNode } from '../viewers/types'
import UploadDrop from './UploadDrop'
import './FocusCanvas.css'

interface Annotation { image: string; note: string }

interface Props {
  entity: Entity | null
  entities: Entity[]
  onChange: () => void
  onFocus: (id: string) => void
  onSelectThread?: (id: string) => void
  onAnnotate?: (a: Annotation) => void
  annotClear?: number
  /** Compact peek variant (chat-first): trim meta + provenance for the rail. */
  compact?: boolean
  /** Hand a request to the Guide (e.g. a Run's Re-run / Discuss → chat). */
  onAsk?: (text: string) => void
  /** "Chat" gesture on a run output — bring the plot (with its image) into chat. */
  onChatResult?: (label: string, thumb?: string, annotation?: { image: string; note: string }) => void
  /** Run view → switch the left rail to the Files tab, deep-linking to a folder. */
  onBrowseFiles?: (path?: string) => void
  /** Per-request project pin for upload routing (dataset "Add files"). */
  projectId?: string
}

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

type PromoteMode =
  | { kind: 'figure-to-claim' }
  | { kind: 'scenario' }

export default function FocusCanvas({ entity, entities, onChange, onFocus, onSelectThread, onAnnotate, annotClear, compact, onAsk, onChatResult, onBrowseFiles, projectId }: Props) {
  const [preview, setPreview] = useState<Preview | null>(null)
  const [promote, setPromote] = useState<PromoteMode | null>(null)
  const [compareOn, setCompareOn] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)

  useEffect(() => {
    setPreview(null)
    if (!entity || entity.type !== 'dataset') return
    let cancelled = false
    fetch(`/api/entities/${encodeURIComponent(entity.id)}/preview?limit=10`)
      .then(r => (r.ok ? r.json() : Promise.reject(r)))
      .then((p: Preview) => { if (!cancelled) setPreview(p) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [entity?.id, entity?.type])

  if (!entity || entity.type === 'workspace') {
    return <WorkspaceCanvas entities={entities} onChange={onChange} onFocus={onFocus} />
  }

  // Create a claim from this result (figure/table), citing it as evidence. In
  // entity-model v3 figures/tables ARE results, and the gesture is figure→Claim
  // directly (the old figure→result→finding→claim chain is gone).
  async function doFigureClaim(text: string) {
    const tid = (entity!.metadata?.thread_id as string) || 'default'
    const r = await fetch('/api/claims', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ statement: text, evidence_ids: [entity!.id], thread_id: tid }),
    })
    if (!r.ok) throw new Error(`claim failed: ${r.status} ${await r.text()}`)
    const created: Entity = await r.json()
    setPromote(null)
    onChange()
    onFocus(created.id)
  }

  // Start a Result (kept observation) seeded with this figure/table; opens it so
  // the user can add a reading, more panels, or notes. Deliberate grouping —
  // never the pin gesture.
  async function groupIntoResult() {
    const e = entity!
    const tid = (e.metadata?.thread_id as string) || 'default'
    const r = await fetch('/api/results', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        thread_id: tid, title: e.title,
        interpretation: (e.metadata?.interpretation as string) || '',
        members: [{ kind: e.type === 'table' ? 'table' : 'figure', ref: e.id }],
      }),
    })
    if (!r.ok) return
    const created: Entity = await r.json()
    onChange()
    onFocus(created.id)
  }

  // Resolve baseline for compare view (when focused on a scenario variant).
  const baseline = entity.scenario_of
    ? entities.find(e => e.id === entity.scenario_of) ?? null
    : null

  return (
    <div className={`focus ${compact ? 'focus--compact' : ''}`}>
      {/* Runs and Results render their own title header; skip the generic one
          to avoid a duplicate type pill + title. */}
      {entity.type !== 'analysis' && entity.type !== 'result' && (
      <div className="focus__header">
        <span className={`focus__type focus__type--${entity.type}`}>{entity.type}</span>
        <h2 className="focus__title">{entity.title}</h2>
        {entity.scenario_of && baseline && (
          <span className="focus__scenario-badge" title={`scenario of ${baseline.title}`}>
            scenario of <em>{baseline.title}</em>
          </span>
        )}
        {baseline && (
          <button
            className={`focus__compare ${compareOn ? 'focus__compare--on' : ''}`}
            onClick={() => setCompareOn(v => !v)}
            title="Compare scenario against its baseline"
          >
            ⇆ Compare
          </button>
        )}
        {entity.type === 'figure' && (
          <button
            className={`focus__clock ${historyOpen ? 'focus__clock--on' : ''}`}
            onClick={() => setHistoryOpen(v => !v)}
            title="Version history"
          >
            <svg width="15" height="15" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10 2a8 8 0 100 16 8 8 0 000-16zm0 14a6 6 0 110-12 6 6 0 010 12zm.5-9H9v4l3.2 1.9.8-1.3-2.5-1.5V7z"/>
            </svg>
          </button>
        )}
        {renderActionButton(entity, setPromote, groupIntoResult)}
      </div>
      )}
      {historyOpen && entity.type === 'figure' && (
        <HistoryDrawer entity={entity} onFocus={onFocus} onClose={() => setHistoryOpen(false)} />
      )}
      <div className="focus__body">
        {entity.type === 'thread'
          ? <ThreadHeader thread={entity} full onChange={onChange} onSwitchThread={onSelectThread ?? onFocus} />
          : compareOn && baseline && entity.type === 'figure'
          ? renderCompareBody(entity, baseline)
          : entity.type === 'figure' && onAnnotate
          ? <AnnotatedFigure entity={entity} onAttach={onAnnotate} clearSignal={annotClear} />
          : renderBody(entity, preview, entities, onFocus, onChange, compact, onAsk, onChatResult, onBrowseFiles, projectId)}
      </div>
      <div className="focus__meta">
        <span title={entity.id}>id {entity.id}</span>
        <span>•</span>
        <span>created {new Date(entity.created_at).toLocaleString()}</span>
        {!compact && entity.parent_entity_id && (
          <>
            <span>•</span>
            <span>parent {entity.parent_entity_id}</span>
          </>
        )}
      </div>

      {!compact && entity.type !== 'analysis' && entity.type !== 'result' && <ProvenancePanel entity={entity} onFocus={onFocus} />}

      {promote?.kind === 'figure-to-claim' && (
        <PromoteDialog
          title={`Create a claim from "${entity.title}"`}
          prompt="State the claim this result supports — keep it sharp, it can be challenged later. (Pre-filled with Guide's read — edit freely.)"
          placeholder="Proneural-marker-high cells form a stable subpopulation, not a transitional state."
          suggest={async () => {
            const r = await fetch(`/api/entities/${encodeURIComponent(entity.id)}/suggest-interpretation`)
            return r.ok ? (await r.json()).text ?? '' : ''
          }}
          onCancel={() => setPromote(null)}
          onSubmit={doFigureClaim}
        />
      )}
    </div>
  )
}

function WorkspaceCanvas({
  entities, onChange, onFocus,
}: {
  entities: Entity[]
  onChange: () => void
  onFocus: (id: string) => void
}) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const datasetCount = entities.filter(e => e.type === 'dataset').length

  async function uploadFile(f: File) {
    setBusy(true); setErr(null)
    try {
      const form = new FormData()
      form.append('file', f)
      const r = await fetch('/api/upload', { method: 'POST', body: form })
      if (!r.ok) throw new Error(await r.text())
      const created: Entity = await r.json()
      onChange(); onFocus(created.id)
    } catch (e) { setErr(String(e)) }
    finally { setBusy(false) }
  }

  async function submitUrl() {
    if (!url.trim() || busy) return
    setBusy(true); setErr(null)
    try {
      const r = await fetch('/api/upload-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim() }),
      })
      if (!r.ok) throw new Error(await r.text())
      const created: Entity = await r.json()
      onChange(); onFocus(created.id); setUrl('')
    } catch (e) { setErr(String(e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="focus focus--workspace">
      <h2 className="focus__title">Workspace</h2>
      <p className="focus__empty-sub">
        Drop a CSV or paste a URL to add data. Once you have something to work
        with, you can talk to Guide below.
      </p>
      <div className="workspace__add">
        <label className="workspace__file-btn" htmlFor="ws-upload">
          Upload file…
          <input
            id="ws-upload"
            type="file"
            style={{ display: 'none' }}
            onChange={e => {
              const f = e.target.files?.[0]
              if (f) uploadFile(f)
            }}
            disabled={busy}
          />
        </label>
        <span className="workspace__or">or</span>
        <input
          className="workspace__url"
          type="url"
          placeholder="paste a URL…"
          value={url}
          onChange={e => setUrl(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') submitUrl() }}
          disabled={busy}
        />
        <button
          className="workspace__url-btn"
          onClick={submitUrl}
          disabled={busy || !url.trim()}
        >
          {busy ? 'Downloading…' : 'Add'}
        </button>
      </div>
      {err && <div className="workspace__err">{err}</div>}
      <div className="workspace__hint">
        {datasetCount === 0
          ? 'no data uploaded yet.'
          : `${datasetCount} dataset${datasetCount === 1 ? '' : 's'} in this project.`}
      </div>
      <div className="workspace__add" style={{ marginTop: 6 }}>
        <button
          className="workspace__file-btn"
          disabled={busy}
          onClick={async () => {
            setBusy(true)
            try {
              const r = await fetch('/api/narratives', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: 'New manuscript section', text: '' }),
              })
              if (r.ok) { const n = await r.json(); onChange(); onFocus(n.id) }
            } finally { setBusy(false) }
          }}
        >
          + New manuscript section
        </button>
      </div>
    </div>
  )
}

function renderCompareBody(scenario: Entity, baseline: Entity) {
  return (
    <div className="focus__compare-grid">
      <div className="focus__compare-pane">
        <div className="focus__compare-label">baseline</div>
        <h3 className="focus__compare-title">{baseline.title}</h3>
        {baseline.artifact_path && (
          <img className="focus__figure" src={baseline.artifact_path} alt={baseline.title} />
        )}
      </div>
      <div className="focus__compare-pane">
        <div className="focus__compare-label">scenario</div>
        <h3 className="focus__compare-title">{scenario.title}</h3>
        {scenario.artifact_path && (
          <img className="focus__figure" src={scenario.artifact_path} alt={scenario.title} />
        )}
        {scenario.metadata?.scenario_description != null && (
          <div className="focus__compare-desc">
            change: {String(scenario.metadata.scenario_description)}
          </div>
        )}
      </div>
    </div>
  )
}

// One optional description for a dataset, editable inline under the title.
// (Replaces the hidden "Edit notes…" menu item — this is where it belongs.)
function DatasetDescription({ entity, onChange }: { entity: Entity; onChange: () => void }) {
  const [val, setVal] = useState(entity.notes ?? '')
  useEffect(() => { setVal(entity.notes ?? '') }, [entity.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const save = () => {
    if ((val ?? '') === (entity.notes ?? '')) return
    fetch(`/api/entities/${encodeURIComponent(entity.id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notes: val }),
    }).then(() => onChange()).catch(() => {})
  }
  // Auto-grow so a long description shows in full instead of being clipped to one line.
  const ref = useRef<HTMLTextAreaElement>(null)
  const grow = () => { const t = ref.current; if (t) { t.style.height = 'auto'; t.style.height = `${t.scrollHeight}px` } }
  useEffect(grow, [val])
  return (
    <textarea ref={ref} className="focus__dataset-desc" value={val} rows={1}
      placeholder="Add a description… (what this dataset is, where it came from)"
      onChange={e => setVal(e.target.value)} onBlur={save} />
  )
}

/** Browse a dataset's directory contents with the shared FileBrowser (folders +
 *  every file), viewing files in a modal — so a folder dataset isn't a single
 *  opaque "file" row. Renders nothing for a dataset with no browsable tree. */
function DatasetFiles({ entity, onFocus, onChatResult, onChange, projectId }: {
  entity: Entity
  onFocus: (id: string) => void
  onChatResult?: (label: string, thumb?: string, annotation?: { image: string; note: string }) => void
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

  // Trust the tree endpoint's authoritative is_directory flag (derived from
  // disk), not the metadata.layout field — older datasets and agent-registered
  // ones often lack the flag even when the artifact_path IS a directory.
  // While the tree is still loading, fall back to the metadata flag so the
  // "Add files" button doesn't flicker.
  const treeFlag = (tree as { is_directory?: boolean } | null)?.is_directory
  const isDirectoryDataset = treeFlag ?? (entity.metadata?.layout === 'directory')
  const empty = !tree || (tree.children?.length ?? 0) === 0
  // Append-mode requires a directory-shaped dataset (backend refuses single-
  // file datasets). Render the section even when empty so the user has the
  // "Add files" landing pad — the dataset was just created.
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

function renderActionButton(
  entity: Entity,
  setPromote: (m: PromoteMode | null) => void,
  onGroup?: () => void,
) {
  // Figures/tables → group into a Result (deliberate), and draft a claim.
  if (entity.type === 'figure' || entity.type === 'table' || entity.type === 'result') {
    return (
      <div className="focus__actions">
        {(entity.type === 'figure' || entity.type === 'table') && onGroup && (
          <button
            className="focus__promote"
            onClick={onGroup}
            title="Start a Result (observation) from this — then add a reading, more panels, or notes"
          >
            ⊞ Group into a result
          </button>
        )}
        <button
          className="focus__promote focus__promote--claim"
          onClick={() => setPromote({ kind: 'figure-to-claim' })}
          title="Draft a claim supported by this result"
        >
          ✦ Create a claim
        </button>
      </div>
    )
  }
  return null
}

function renderBody(
  e: Entity,
  preview: Preview | null,
  entities: Entity[],
  onFocus: (id: string) => void,
  onChange: () => void,
  compact?: boolean,
  onAsk?: (t: string) => void,
  onChatResult?: (label: string, thumb?: string, annotation?: { image: string; note: string }) => void,
  onBrowseFiles?: (path?: string) => void,
  projectId?: string,
) {
  switch (e.type) {
    case 'figure':
      return e.artifact_path ? (
        <img className="focus__figure" src={e.artifact_path} alt={e.title} />
      ) : (
        <p className="focus__placeholder">No artifact attached.</p>
      )

    case 'dataset':
      return (
        <div className="focus__dataset">
          <DatasetDescription entity={e} onChange={onChange} />
          <div className="focus__rows">
            <div className="focus__row">
              <span className="focus__row-label">file</span>
              <code className="focus__row-val">{e.artifact_path ?? '—'}</code>
            </div>
            {e.metadata?.source ? (
              <div className="focus__row">
                <span className="focus__row-label">source</span>
                <span className="focus__row-val">{String(e.metadata.source)}</span>
              </div>
            ) : null}
            {e.metadata?.organism ? (
              <div className="focus__row">
                <span className="focus__row-label">organism</span>
                <span className="focus__row-val">{String(e.metadata.organism)}</span>
              </div>
            ) : null}
            {e.metadata?.size_bytes != null && (
              <div className="focus__row">
                <span className="focus__row-label">size</span>
                <span className="focus__row-val">{formatBytes(Number(e.metadata.size_bytes))}</span>
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
          {preview?.kind === 'table' && (
            <PreviewTable entityId={e.id} pageSize={15} />
          )}
          {preview?.kind === 'error' && (
            <div className="focus__placeholder">preview error: {preview.error}</div>
          )}
          <DatasetFiles entity={e} onFocus={onFocus} onChatResult={onChatResult} onChange={onChange} projectId={projectId} />
        </div>
      )

    case 'analysis':
      return <RunView run={e} entities={entities} onFocus={onFocus} onChange={onChange} onAsk={onAsk} onChatResult={onChatResult} onBrowseFiles={onBrowseFiles} />

    case 'result':
      return <ResultView result={e} entities={entities} onFocus={onFocus} onChange={onChange} onAsk={onAsk} onChatResult={onChatResult} />


    case 'result': {
      const interpretation = (e.metadata?.interpretation as string) ?? ''
      const evidence = (e.metadata?.evidence_figure as string) ?? null
      const evidenceEntity = evidence ? entities.find(x => x.id === evidence) : null
      return (
        <div className="focus__abstract">
          <p className="focus__interpretation">{interpretation}</p>
          {evidenceEntity && (
            <div className="focus__chain">
              <div className="focus__chain-head">EVIDENCE</div>
              <EntityRow ent={evidenceEntity} onClick={() => onFocus(evidenceEntity.id)} />
            </div>
          )}
        </div>
      )
    }

    case 'finding':
      return (
        <FindingBody finding={e} entities={entities} onFocus={onFocus} onChange={onChange} />
      )

    case 'note':
      return (
        <div className="focus__abstract">
          <p className="focus__interpretation">{(e.metadata?.text as string) ?? e.notes ?? e.title}</p>
          <p className="focus__placeholder">Kept from the conversation.</p>
        </div>
      )

    case 'claim':
      return <ClaimView claim={e} entities={entities} onFocus={onFocus} onChange={onChange} compact={compact} />

    case 'table':
      return <PreviewTable entityId={e.id} pageSize={25} />

    case 'narrative':
      return <NarrativeBody entity={e} onChange={onChange} />

    default:
      return (
        <p className="focus__placeholder">{entityTypeBlurb(e.type)}</p>
      )
  }
}

/** Paginated CSV/TSV preview. Fetches a window of rows per page via the
 *  preview endpoint's limit/offset. Used for datasets and table entities. */
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

function NarrativeBody({ entity, onChange }: { entity: Entity; onChange: () => void }) {
  const [text, setText] = useState((entity.metadata?.text as string) ?? '')
  const [dirty, setDirty] = useState(false)
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

function HistoryDrawer({
  entity, onFocus, onClose,
}: {
  entity: Entity; onFocus: (id: string) => void; onClose: () => void
}) {
  const [versions, setVersions] = useState<Entity[]>([])
  useEffect(() => {
    let cancelled = false
    fetch(`/api/entities/${encodeURIComponent(entity.id)}/history`)
      .then(r => (r.ok ? r.json() : Promise.reject(r)))
      .then((v: Entity[]) => { if (!cancelled) setVersions(v) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [entity.id])

  if (versions.length <= 1) {
    return (
      <div className="history">
        <div className="history__head">Version history <button className="history__close" onClick={onClose}>×</button></div>
        <div className="history__empty">This is the only version so far.</div>
      </div>
    )
  }

  return (
    <div className="history">
      <div className="history__head">
        {versions.length} versions
        <button className="history__close" onClick={onClose}>×</button>
      </div>
      <div className="history__strip">
        {versions.map((v, i) => (
          <button
            key={v.id}
            className={`history__thumb ${v.id === entity.id ? 'history__thumb--current' : ''}`}
            onClick={() => onFocus(v.id)}
            title={new Date(v.created_at).toLocaleString()}
          >
            {v.artifact_path && <img src={v.artifact_path} alt={v.title} />}
            <div className="history__thumb-label">
              {i === 0 ? 'current' : `v${versions.length - i}`}
              <span className="history__thumb-date">
                {new Date(v.created_at).toLocaleDateString()}
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

interface ProvNode { id: string; type: string; title: string; rel: string; depth: number }

function ProvenancePanel({ entity, onFocus }: { entity: Entity; onFocus: (id: string) => void }) {
  const [data, setData] = useState<{ upstream: ProvNode[]; downstream: ProvNode[] } | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    setData(null)
    if (entity.type === 'workspace') return
    let cancelled = false
    fetch(`/api/entities/${encodeURIComponent(entity.id)}/provenance`)
      .then(r => (r.ok ? r.json() : Promise.reject(r)))
      .then(d => { if (!cancelled) setData(d) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [entity.id, entity.type])

  const up = data?.upstream ?? []
  const down = data?.downstream ?? []
  if (up.length === 0 && down.length === 0) return null

  return (
    <div className={`prov ${open ? 'prov--open' : ''}`}>
      <button className="prov__toggle" onClick={() => setOpen(v => !v)}>
        <span className="prov__chev">{open ? '▾' : '▸'}</span>
        Provenance
        <span className="prov__counts">
          {up.length > 0 && `${up.length} up`}
          {up.length > 0 && down.length > 0 && ' · '}
          {down.length > 0 && `${down.length} down`}
        </span>
      </button>
      {open && (
        <div className="prov__cols">
          <div className="prov__col">
            <div className="prov__col-head">Made from</div>
            {up.length === 0 && <div className="prov__empty">— nothing upstream</div>}
            {up.map(n => (
              <button key={n.id} className="prov__row" onClick={() => onFocus(n.id)}
                      style={{ paddingLeft: 8 + (n.depth - 1) * 12 }}>
                <span className={`focus__type focus__type--${n.type}`}>{n.type}</span>
                <span className="prov__title">{n.title}</span>
              </button>
            ))}
          </div>
          <div className="prov__col">
            <div className="prov__col-head">Used by</div>
            {down.length === 0 && <div className="prov__empty">— nothing downstream</div>}
            {down.map(n => (
              <button key={n.id} className="prov__row" onClick={() => onFocus(n.id)}
                      style={{ paddingLeft: 8 + (n.depth - 1) * 12 }}>
                <span className={`focus__type focus__type--${n.type}`}>{n.type}</span>
                <span className="prov__title">{n.title}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

const MATURITY = ['draft', 'candidate', 'checked', 'manuscript'] as const

interface Caveat { text: string; source?: string }

function FindingBody({
  finding, entities, onFocus, onChange,
}: {
  finding: Entity
  entities: Entity[]
  onFocus: (id: string) => void
  onChange: () => void
}) {
  const meta = finding.metadata ?? {}
  const [picking, setPicking] = useState(false)
  const [summary, setSummary] = useState((meta.summary as string) ?? (meta.text as string) ?? '')
  const [editingSummary, setEditingSummary] = useState(false)
  const [newCaveat, setNewCaveat] = useState('')

  useEffect(() => {
    setSummary((finding.metadata?.summary as string) ?? (finding.metadata?.text as string) ?? '')
  }, [finding.id])

  const status = (meta.maturity as string) ?? 'candidate'
  const caveats: Caveat[] = (meta.caveats as Caveat[]) ?? []
  // Evidence = explicit evidence list (figures/tables) ∪ supporting results.
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
  function removeCaveat(i: number) {
    patch({ caveats: caveats.filter((_, j) => j !== i) })
  }

  return (
    <div className="fv">
      {/* Maturity ladder */}
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

      {/* Summary (editable) */}
      <div className="fv-section-label">Summary <button className="fv-edit" onClick={() => setEditingSummary(v => !v)}>{editingSummary ? 'done' : 'edit'}</button></div>
      {editingSummary ? (
        <textarea className="fv-summary-edit" value={summary} autoFocus rows={4}
          onChange={e => setSummary(e.target.value)}
          onBlur={() => { setEditingSummary(false); if (summary !== (meta.summary ?? meta.text)) patch({ summary }) }} />
      ) : (
        <p className="fv-summary" onClick={() => setEditingSummary(true)}>{summary || <em className="focus__placeholder">Click to add a summary…</em>}</p>
      )}

      {/* Evidence */}
      <div className="fv-section-label">Evidence ({evidenceEnts.length})
        <button className="fv-edit" onClick={() => setPicking(v => !v)} disabled={candidates.length === 0}>+ add</button>
      </div>
      {evidenceEnts.length === 0 && <div className="focus__placeholder">No evidence linked yet.</div>}
      {evidenceEnts.map(s => (
        <div key={s.id} className="focus__chain-row-wrap">
          <EntityRow ent={s} onClick={() => onFocus(s.id)} />
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

      {/* Caveats */}
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

function EntityRow({ ent, onClick }: { ent: Entity; onClick: () => void }) {
  return (
    <button className="focus__chain-row" onClick={onClick} type="button">
      <span className={`focus__type focus__type--${ent.type}`}>{ent.type}</span>
      <span className="focus__chain-title">{ent.title}</span>
      <span className="focus__chain-arrow">↗</span>
    </button>
  )
}

function entityTypeBlurb(t: string): string {
  switch (t) {
    case 'table':     return 'Tabular artifact view coming in a later phase.'
    case 'narrative': return 'A manuscript section composed from claims.'
    default:          return 'Detail view not yet implemented for this entity type.'
  }
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`
}
