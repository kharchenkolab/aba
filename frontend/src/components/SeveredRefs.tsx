/**
 * The reader for `metadata.severed_refs` — the stamp the delete route writes on
 * every SURVIVING neighbor of a hard-deleted entity.
 *
 * The stamp exists so a gap in the graph is RECORDED rather than silent
 * (silence is a claim: "nothing was here"). Until this component it had no
 * reader anywhere, so the honesty was write-only: a figure whose producing run
 * was deleted, or a member kept through a cascade, still showed the user a
 * dangling nothing. Rendered above the per-type body so it reaches every
 * entity type, including the three that supply their own header.
 */
import './SeveredRefs.css'

export interface SeveredRef {
  id: string
  type?: string
  title?: string
  at?: number
  rel?: string
  dir?: string
}

const SHOWN = 3

export default function SeveredRefs({ refs }: { refs?: SeveredRef[] | null }) {
  const list = (refs ?? []).filter(r => r && typeof r === 'object')
  if (list.length === 0) return null
  const head = list.slice(0, SHOWN)
  const rest = list.length - head.length
  return (
    <div className="severed" role="note">
      <span className="severed__icon" aria-hidden>⊘</span>
      <span className="severed__text">
        {list.length === 1 ? 'A reference was removed: ' : 'References were removed: '}
        {head.map((r, i) => (
          <span key={r.id ?? i}>
            {i > 0 && ', '}
            <b>{r.title || r.id || 'an entity'}</b>
            {r.type ? ` (${r.type}` : ''}{r.type && r.rel ? `, ${r.rel}` : r.rel ? ` (${r.rel}` : ''}
            {r.type || r.rel ? ')' : ''}
          </span>
        ))}
        {rest > 0 && <> and {rest} more</>}
        {' — deleted, so this record no longer resolves.'}
      </span>
    </div>
  )
}
