/**
 * Safety ledger strip (more_weft_ui.md §1) — one glanceable answer to "is
 * anything in this project going to disappear?", rendered on the Data /
 * Results section head.
 *
 * QUIESCENCE CONTRACT (the local-only snapshot test rides this): with every
 * item safe and no remote sites involved, this component renders NOTHING —
 * a single-machine project must look exactly like pre-multi-site aba.
 * States come verbatim from GET /api/projects/{pid}/data-ledger — the same
 * query the Guide's data_safety_summary tool uses, so chat and UI agree.
 */
import { useEffect, useState } from 'react'
import './LedgerStrip.css'

export interface LedgerItem {
  entity_id: string; kind: string; title?: string | null
  state: 'safe' | 'at_risk' | 'changed' | 'unknown' | string
  site?: string | null; bytes?: number | null; why: string
  /** the id resolves to an entity in THIS project's graph, so focusing it
   *  will land somewhere — a keep whose run lives elsewhere never did */
  linkable?: boolean
  /** what would actually fix this, when anything would */
  remedy?: { action: string; label: string; note?: string; targets?: string[] }
}
export interface Ledger {
  items: LedgerItem[]
  totals: { items: number; safe: number; at_risk: number; changed: number; unknown: number }
  remote_sites: string[]
  multi_site: boolean
  /** retention index unreachable — kept-result rows may be MISSING */
  degraded?: boolean
  degraded_note?: string
  /** kept results this project cannot claim (weft's retention index is
   *  workspace-wide). Counted, never listed — silently dropping a genuinely
   *  at-risk result because the user is standing in another project is the
   *  same class of dishonesty as going quiet during an outage. */
  elsewhere?: { items: number; at_risk: number }
}

const STATE_WORD: Record<string, string> = {
  at_risk: 'at risk', changed: 'source changed', unknown: 'unknown',
}

export default function LedgerStrip({ projectId, onFocus, onPrefill, fingerprint }: {
  projectId?: string
  onFocus?: (id: string) => void
  /** hand a repair to the Guide, prefilled and unsent. The ledger deliberately
   *  has no button that moves bytes itself: one way to do a thing. */
  onPrefill?: (text: string) => void
  /** cheap change signal from the entity list — the strip must REFETCH when
   *  the world changes (a mid-session registration left it stale-quiet:
   *  browser-study finding), not only on mount. */
  fingerprint?: string
}) {
  const [led, setLed] = useState<Ledger | null>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    let dead = false
    fetch(`/api/projects/${encodeURIComponent(projectId || 'default')}/data-ledger`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (!dead) setLed(d) })
      .catch(() => { /* no ledger → render nothing */ })
    return () => { dead = true }
  }, [projectId, fingerprint])

  if (!led) return null
  const t = led.totals
  const attention = t.at_risk + t.changed + t.unknown
  // Kept results this project cannot claim. Weft's retention index is one per
  // WORKSPACE, so the rollup used to list every run the user had ever kept, in
  // every project. Scoping that to the project is right — but a result at risk
  // in a project you are not standing in must not vanish with it.
  const elsewhereRisk = led.elsewhere?.at_risk ?? 0
  // Degraded is NEVER quiet: quiet means "all safe", and during a substrate
  // outage the kept-result rows are missing from the ledger — saying nothing
  // would claim safety we cannot assess.
  if (led.degraded) {
    return (
      <div className="ledger">
        <div className="ledger__line">
          <span className="ledger__flag">⚠ {led.degraded_note
            || 'data-safety status unavailable — compute substrate unreachable'}</span>
        </div>
      </div>
    )
  }
  // Quiet by default: ALL SAFE → say nothing, multi-site included. "86 items
  // · 86 safe (some on …)" was chrome answering a question nobody asked —
  // where an item lives belongs on its card; this strip exists to flag what
  // needs ATTENTION, and quiet means safe (the quiescence contract above).
  if (!attention && !elsewhereRisk) return null

  const flagged = led.items.filter(i => i.state !== 'safe')
  const ask = (i: LedgerItem) => {
    const name = i.title ? `"${i.title}" (${i.entity_id})` : i.entity_id
    onPrefill?.(`Secure this at-risk result: ${name}. ${i.why}. `
      + `Please copy its kept files somewhere durable and tell me where they ended up.`)
  }
  return (
    <div className="ledger">
      {attention > 0 && (
        <div className="ledger__line">
          <span className="ledger__lead">
            {attention} of {t.items} item{t.items === 1 ? '' : 's'} need{attention === 1 ? 's' : ''} attention
          </span>
          {t.at_risk > 0 && <span className="ledger__flag ledger__flag--risk">{t.at_risk} at risk</span>}
          {t.changed > 0 && <span className="ledger__flag ledger__flag--changed">{t.changed} source changed</span>}
          {t.unknown > 0 && <span className="ledger__flag">{t.unknown} unknown</span>}
          <button className="ledger__review" onClick={() => setOpen(o => !o)}>
            {open ? 'Hide' : 'Review'}
          </button>
        </div>
      )}
      {elsewhereRisk > 0 && (
        <div className="ledger__line ledger__why">
          {elsewhereRisk} kept result{elsewhereRisk === 1 ? '' : 's'} outside this project
          {elsewhereRisk === 1 ? ' needs' : ' need'} attention — open that project to see them
        </div>
      )}
      {open && attention > 0 && (
        <ul className="ledger__list">
          {flagged.map(i => (
            <li key={i.entity_id}>
              <button className="ledger__item" disabled={!i.linkable || !onFocus}
                onClick={() => onFocus?.(i.entity_id)}>
                {i.title || i.entity_id}
              </button>
              <span className="ledger__why"> — {STATE_WORD[i.state] || i.state}: {i.why}</span>
              {i.remedy && onPrefill && (
                <button className="ledger__fix" onClick={() => ask(i)}
                  title={i.remedy.note}>Ask the Guide to fix this</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
