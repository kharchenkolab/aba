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
}
export interface Ledger {
  items: LedgerItem[]
  totals: { items: number; safe: number; at_risk: number; changed: number; unknown: number }
  remote_sites: string[]
  multi_site: boolean
  /** retention index unreachable — kept-result rows may be MISSING */
  degraded?: boolean
  degraded_note?: string
}

const STATE_WORD: Record<string, string> = {
  at_risk: 'at risk', changed: 'source changed', unknown: 'unknown',
}

export default function LedgerStrip({ projectId, onFocus, fingerprint }: {
  projectId?: string
  onFocus?: (id: string) => void
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
  // Quiet by default: all safe AND single-machine → say nothing at all.
  if (!attention && !led.multi_site) return null

  const flagged = led.items.filter(i => i.state !== 'safe')
  return (
    <div className="ledger">
      <div className="ledger__line">
        <span>
          {t.items} item{t.items === 1 ? '' : 's'} · {t.safe} safe
          {led.multi_site && ` (some on ${led.remote_sites.join(', ')})`}
        </span>
        {t.at_risk > 0 && <span className="ledger__flag ledger__flag--risk">{t.at_risk} at risk</span>}
        {t.changed > 0 && <span className="ledger__flag ledger__flag--changed">{t.changed} source changed</span>}
        {t.unknown > 0 && <span className="ledger__flag">{t.unknown} unknown</span>}
        {attention > 0 && (
          <button className="ledger__review" onClick={() => setOpen(o => !o)}>
            {open ? 'Hide' : 'Review'}
          </button>
        )}
      </div>
      {open && attention > 0 && (
        <ul className="ledger__list">
          {flagged.map(i => (
            <li key={i.entity_id}>
              <button className="ledger__item" disabled={i.kind !== 'dataset' || !onFocus}
                onClick={() => onFocus?.(i.entity_id)}>
                {i.title || i.entity_id}
              </button>
              <span className="ledger__why"> — {STATE_WORD[i.state] || i.state}: {i.why}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
