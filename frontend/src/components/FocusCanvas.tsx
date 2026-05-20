import { useEffect, useState } from 'react'
import type { Entity } from '../types'
import './FocusCanvas.css'

interface Props {
  entity: Entity | null
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

export default function FocusCanvas({ entity }: Props) {
  const [preview, setPreview] = useState<Preview | null>(null)

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
    return (
      <div className="focus focus--empty">
        <p className="focus__empty-title">No entity focused</p>
        <p className="focus__empty-sub">
          Click a figure, table, or dataset in the tree to scope the conversation
          to it. Or just talk to Guide here about the project as a whole.
        </p>
      </div>
    )
  }

  return (
    <div className="focus">
      <div className="focus__header">
        <span className={`focus__type focus__type--${entity.type}`}>{entity.type}</span>
        <h2 className="focus__title">{entity.title}</h2>
      </div>
      <div className="focus__body">{renderBody(entity, preview)}</div>
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
    </div>
  )
}

function renderBody(e: Entity, preview: Preview | null) {
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
          {e.producing_code && (
            <pre className="focus__code">{e.producing_code}</pre>
          )}
        </div>
      )

    case 'result':
    case 'finding':
    case 'claim':
    case 'narrative':
    case 'table':
    default:
      return (
        <p className="focus__placeholder">
          {entityTypeBlurb(e.type)}
        </p>
      )
  }
}

function entityTypeBlurb(t: string): string {
  switch (t) {
    case 'table':
      return 'Tabular artifact view coming in a later phase.'
    case 'result':
      return 'A figure interpreted with a one-line claim. (Promote-to-result lands in Phase 3.)'
    case 'finding':
      return 'A synthesis of supporting results. (Phase 3.)'
    case 'claim':
      return 'A publishable assertion backed by findings. (Phase 3.)'
    case 'narrative':
      return 'A manuscript section composed from claims. (Phase 3.)'
    default:
      return 'Detail view not yet implemented for this entity type.'
  }
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`
}
