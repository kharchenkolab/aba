import { useEffect, useState } from 'react'
import type { Entity } from '../types'
import PromoteDialog from './PromoteDialog'
import './FocusCanvas.css'

interface Props {
  entity: Entity | null
  entities: Entity[]
  onChange: () => void
  onFocus: (id: string) => void
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
  | { kind: 'figure-to-result' }
  | { kind: 'result-to-finding' }
  | { kind: 'finding-to-claim' }
  | { kind: 'scenario' }

export default function FocusCanvas({ entity, entities, onChange, onFocus }: Props) {
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

  async function doFigurePromote(text: string) {
    const r = await fetch(
      `/api/entities/${encodeURIComponent(entity!.id)}/promote-to-result`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interpretation: text }),
      },
    )
    if (!r.ok) throw new Error(`promote failed: ${r.status} ${await r.text()}`)
    const created: Entity = await r.json()
    setPromote(null)
    onChange()
    onFocus(created.id)
  }

  async function doResultPromote(text: string) {
    // Phase-3 simple form: a finding from this single result. Multi-result
    // findings come from the finding's own canvas after creation (P3.4).
    const r = await fetch('/api/findings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ result_ids: [entity!.id], text }),
    })
    if (!r.ok) throw new Error(`promote failed: ${r.status} ${await r.text()}`)
    const created: Entity = await r.json()
    setPromote(null)
    onChange()
    onFocus(created.id)
  }

  async function doFindingPromote(text: string) {
    const r = await fetch('/api/claims', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ finding_ids: [entity!.id], text }),
    })
    if (!r.ok) throw new Error(`promote failed: ${r.status} ${await r.text()}`)
    const created: Entity = await r.json()
    setPromote(null)
    onChange()
    onFocus(created.id)
  }

  async function doScenario(description: string) {
    const r = await fetch(
      `/api/entities/${encodeURIComponent(entity!.id)}/create-scenario`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description }),
      },
    )
    if (!r.ok) throw new Error(`scenario failed: ${r.status} ${await r.text()}`)
    const created: Entity = await r.json()
    setPromote(null)
    onChange()
    onFocus(created.id)
  }

  // Resolve baseline for compare view (when focused on a scenario variant).
  const baseline = entity.scenario_of
    ? entities.find(e => e.id === entity.scenario_of) ?? null
    : null

  return (
    <div className="focus">
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
        {renderActionButton(entity, setPromote)}
      </div>
      {historyOpen && entity.type === 'figure' && (
        <HistoryDrawer entity={entity} onFocus={onFocus} onClose={() => setHistoryOpen(false)} />
      )}
      <div className="focus__body">
        {compareOn && baseline && entity.type === 'figure'
          ? renderCompareBody(entity, baseline)
          : renderBody(entity, preview, entities, onFocus, onChange)}
      </div>
      <div className="focus__meta">
        <span title={entity.id}>id {entity.id}</span>
        <span>•</span>
        <span>created {new Date(entity.created_at).toLocaleString()}</span>
        {entity.parent_entity_id && (
          <>
            <span>•</span>
            <span>parent {entity.parent_entity_id}</span>
          </>
        )}
      </div>

      <ProvenancePanel entity={entity} onFocus={onFocus} />

      {promote?.kind === 'figure-to-result' && (
        <PromoteDialog
          title={`Promote "${entity.title}" to a result`}
          prompt="What does this figure tell you? One or two lines, in your own voice."
          placeholder="Sample S4 has mt_fraction 0.13, ~3× higher than other samples — likely doublet contamination."
          onCancel={() => setPromote(null)}
          onSubmit={doFigurePromote}
        />
      )}
      {promote?.kind === 'result-to-finding' && (
        <PromoteDialog
          title={`Lift "${entity.title}" into a finding`}
          prompt="A finding is the synthesis across one or more results. State it crisply."
          placeholder="Sample-level QC consistently flags donor S4 across mt_fraction and viability metrics."
          onCancel={() => setPromote(null)}
          onSubmit={doResultPromote}
        />
      )}
      {promote?.kind === 'finding-to-claim' && (
        <PromoteDialog
          title={`Lift "${entity.title}" into a claim`}
          prompt="A claim is publishable — keep it sharp. It can be challenged later."
          placeholder="Sample S4 must be excluded from downstream analysis due to consistent QC failures."
          onCancel={() => setPromote(null)}
          onSubmit={doFindingPromote}
        />
      )}
      {promote?.kind === 'scenario' && (
        <PromoteDialog
          title={`Scenario from "${entity.title}"`}
          prompt="Describe the variation you want to try. Guide will modify the producing code and run it. The new figure will sit alongside this one with a Compare toggle."
          placeholder="What if we cap n_genes at 2500? — or — exclude sample S4 — or — use mt_fraction cutoff 0.10"
          onCancel={() => setPromote(null)}
          onSubmit={doScenario}
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

function renderActionButton(
  entity: Entity,
  setPromote: (m: PromoteMode | null) => void,
) {
  if (entity.type === 'figure') {
    return (
      <div className="focus__actions">
        <button
          className="focus__promote"
          onClick={() => setPromote({ kind: 'scenario' })}
          title="Create a scenario variant by modifying this figure's parameters"
        >
          ⤴ What if…
        </button>
        <button
          className="focus__promote"
          onClick={() => setPromote({ kind: 'figure-to-result' })}
          title="Capture an interpretation of this figure as a result"
        >
          ↑ Promote to result
        </button>
      </div>
    )
  }
  if (entity.type === 'result') {
    return (
      <button
        className="focus__promote"
        onClick={() => setPromote({ kind: 'result-to-finding' })}
      >
        ↑ Lift to finding
      </button>
    )
  }
  if (entity.type === 'finding') {
    return (
      <button
        className="focus__promote"
        onClick={() => setPromote({ kind: 'finding-to-claim' })}
      >
        ↑ Lift to claim
      </button>
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
          <div className="focus__rows">
            <div className="focus__row">
              <span className="focus__row-label">file</span>
              <code className="focus__row-val">{e.artifact_path ?? '—'}</code>
            </div>
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
            <div className="focus__preview-wrap">
              <table className="focus__preview-table">
                <thead>
                  <tr>{preview.columns.map(c => <th key={c}>{c}</th>)}</tr>
                </thead>
                <tbody>
                  {preview.rows.map((row, i) => (
                    <tr key={i}>
                      {row.map((v, j) => (
                        <td key={j}>{v == null ? <em>·</em> : String(v)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="focus__preview-foot">
                showing {preview.shown} of {preview.total_rows} rows
              </div>
            </div>
          )}
          {preview?.kind === 'error' && (
            <div className="focus__placeholder">preview error: {preview.error}</div>
          )}
        </div>
      )

    case 'analysis':
      return (
        <div className="focus__analysis">
          <p className="focus__placeholder">
            A run that produced one or more artifacts.
            {e.producing_params && ` Params: ${JSON.stringify(e.producing_params)}.`}
          </p>
          {e.producing_code && <pre className="focus__code">{e.producing_code}</pre>}
        </div>
      )

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

    case 'claim': {
      const text = (e.metadata?.text as string) ?? ''
      const findIds = (e.metadata?.supporting_findings as string[]) ?? []
      const findEnts = findIds
        .map(id => entities.find(x => x.id === id))
        .filter((x): x is Entity => !!x)
      return (
        <div className="focus__abstract">
          <p className="focus__interpretation">{text}</p>
          {findEnts.length > 0 && (
            <div className="focus__chain">
              <div className="focus__chain-head">SUPPORTING FINDINGS</div>
              {findEnts.map(f => (
                <EntityRow key={f.id} ent={f} onClick={() => onFocus(f.id)} />
              ))}
            </div>
          )}
        </div>
      )
    }

    case 'table':
    case 'narrative':
    default:
      return (
        <p className="focus__placeholder">{entityTypeBlurb(e.type)}</p>
      )
  }
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

function FindingBody({
  finding, entities, onFocus, onChange,
}: {
  finding: Entity
  entities: Entity[]
  onFocus: (id: string) => void
  onChange: () => void
}) {
  const [picking, setPicking] = useState(false)
  const text = (finding.metadata?.text as string) ?? ''
  const supportingIds = (finding.metadata?.supporting_results as string[]) ?? []
  const supportingEnts = supportingIds
    .map(id => entities.find(x => x.id === id))
    .filter((x): x is Entity => !!x)

  const candidates = entities.filter(
    e => e.type === 'result' && !supportingIds.includes(e.id) && e.status !== 'archived',
  )

  async function addResult(resultId: string) {
    await fetch(`/api/findings/${encodeURIComponent(finding.id)}/add-result`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ result_id: resultId }),
    })
    setPicking(false)
    onChange()
  }

  async function removeResult(resultId: string) {
    await fetch(`/api/findings/${encodeURIComponent(finding.id)}/remove-result`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ result_id: resultId }),
    })
    onChange()
  }

  return (
    <div className="focus__abstract">
      <p className="focus__interpretation">{text}</p>
      <div className="focus__chain">
        <div className="focus__chain-head">
          SUPPORTING RESULTS
          <button
            className="focus__chain-add"
            onClick={() => setPicking(v => !v)}
            disabled={candidates.length === 0}
            title={candidates.length === 0 ? 'No other results to add' : 'Add a supporting result'}
          >
            + Add
          </button>
        </div>
        {supportingEnts.length === 0 && (
          <div className="focus__placeholder">No supporting results yet.</div>
        )}
        {supportingEnts.map(s => (
          <div key={s.id} className="focus__chain-row-wrap">
            <EntityRow ent={s} onClick={() => onFocus(s.id)} />
            <button
              className="focus__chain-remove"
              onClick={() => removeResult(s.id)}
              title="Remove from finding"
            >
              ×
            </button>
          </div>
        ))}
        {picking && (
          <div className="focus__picker">
            <div className="focus__picker-head">Add a result</div>
            {candidates.map(c => (
              <button key={c.id} className="focus__picker-row" onClick={() => addResult(c.id)}>
                <span className="focus__type focus__type--result">result</span>
                {c.title}
              </button>
            ))}
          </div>
        )}
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
