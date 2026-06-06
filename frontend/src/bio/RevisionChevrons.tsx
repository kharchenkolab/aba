/**
 * RevisionChevrons — overlays prev/next chevrons + a "rev N of M" badge
 * on top of a figure (or table) artifact view. Stage 5 frontend wiring
 * for misc/exec_records_and_versioning.md.
 *
 * Hidden entirely when the entity has no revisions (chain length ≤ 1).
 * Clicking a chevron calls `onFocus(otherId)` so the parent (FocusCanvas)
 * navigates to the sibling revision in-place.
 */
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { Entity } from '../types'


/** Shape returned by GET /api/entities/{id}/revisions. */
interface RevisionsResponse {
  chain: Entity[]
  position: number
  prev: string | null
  next: string | null
}


/** Fetch the revision chain for `entity_id`. Re-runs when the id
 *  changes. Returns null while loading or on error (the chevron strip
 *  hides itself in either case). */
export function useFigureHistory(entity_id: string): RevisionsResponse | null {
  const [data, setData] = useState<RevisionsResponse | null>(null)
  useEffect(() => {
    if (!entity_id) {
      setData(null)
      return
    }
    let cancelled = false
    setData(null)
    fetch(`/api/entities/${encodeURIComponent(entity_id)}/revisions`)
      .then(r => (r.ok ? r.json() : Promise.reject(r)))
      .then((body: RevisionsResponse) => {
        if (!cancelled) setData(body)
      })
      .catch(() => { /* silent — chevrons just don't render */ })
    return () => { cancelled = true }
  }, [entity_id])
  return data
}


interface ChevronsProps {
  entity_id: string
  onFocus: (id: string) => void
  /** Wrapped artifact. The chevrons are positioned absolutely over this
   *  container; the parent should give it `position: relative` (handled
   *  via the focus__figure-wrap CSS class). */
  children: ReactNode
}


export default function RevisionChevrons({ entity_id, onFocus, children }: ChevronsProps) {
  const hist = useFigureHistory(entity_id)
  // Only render the overlay when there's more than one revision in the
  // chain. The wrapper itself stays so we don't reflow the figure.
  const has_chain = hist != null && hist.chain.length > 1
  const pos = hist?.position ?? 0
  const total = hist?.chain.length ?? 1
  return (
    <div className="focus__figure-wrap">
      {children}
      {has_chain && (
        <>
          {hist.prev && (
            <button
              type="button"
              className="rev-chevron rev-chevron--prev"
              title={`Previous revision (${pos + 2} of ${total})`}
              onClick={() => onFocus(hist.prev!)}
              aria-label="Previous revision"
            >
              ‹
            </button>
          )}
          {hist.next && (
            <button
              type="button"
              className="rev-chevron rev-chevron--next"
              title={`Next revision (${pos} of ${total})`}
              onClick={() => onFocus(hist.next!)}
              aria-label="Next revision"
            >
              ›
            </button>
          )}
          <span className="rev-chevron__badge"
                title={`Revision ${total - pos} of ${total}`}>
            rev {total - pos}/{total}
          </span>
        </>
      )}
    </div>
  )
}
