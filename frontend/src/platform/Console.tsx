/**
 * Console tab — the live system X-ray in the (i) drawer.
 *
 * Renders the shared console feed store (console.ts): chat-turn activity,
 * out-of-band notifications and substrate events (transfers, jobs, kernels,
 * chunk streaming) in one dense timeline. Designed for a ~320px column:
 *
 *   • no time column — hover a row for the full timestamp; faint dividers
 *     mark >60s gaps (that's how time is actually looked up);
 *   • no aligned columns at all — each row is a wrapped paragraph with a
 *     hanging indent: category glyph (severity-colored), site chip (stable
 *     per-site hue), verb + summary, then a dim trailing fact cluster
 *     (bytes, duration, status);
 *   • facet filters instead of a detail-level ladder: category chips ×
 *     site chips × errors-only × free-text search;
 *   • a tool call is ONE row that updates in place (live pulse while open,
 *     then status + duration) — chunk/progress ticks fold into it;
 *   • click a row to expand the full structured payload; follow-tail with
 *     an "N new" pill when scrolled up.
 */
import React, { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import {
  CATEGORIES, consoleRows, consoleSites, filterRows, subscribeConsole,
  type Category, type ConsoleFilter, type ConsoleRow,
} from '../console'
import './Console.css'

const GLYPH: Record<Category, string> = {
  agent: '✦', tool: '⚙', run: '▶', data: '⇅',
  env: '⬡', compute: '⌁', serve: '◍', system: '·',
}

/** Render at most this many (most recent) rows — a 2000-row feed stays
 *  responsive without virtualization; the header says when we clip. */
const RENDER_CAP = 750
const GAP_MS = 60_000

const FACETS_KEY = 'aba.console.facets'

function loadFacets(): { cats: Category[] | null; errorsOnly: boolean } {
  try {
    const raw = JSON.parse(localStorage.getItem(FACETS_KEY) || '{}')
    const cats = Array.isArray(raw.cats)
      ? raw.cats.filter((c: string) => (CATEGORIES as string[]).includes(c))
      : null
    return { cats: cats?.length ? cats : null, errorsOnly: !!raw.errorsOnly }
  } catch { return { cats: null, errorsOnly: false } }
}

function saveFacets(cats: Set<Category> | null, errorsOnly: boolean): void {
  try {
    localStorage.setItem(FACETS_KEY, JSON.stringify(
      { cats: cats ? [...cats] : null, errorsOnly }))
  } catch { /* storage unavailable — facets just don't persist */ }
}

export function siteHue(site: string): number {
  let h = 0
  for (let i = 0; i < site.length; i++) h = (h * 31 + site.charCodeAt(i)) >>> 0
  return h % 360
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n}B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)}MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(1)}GB`
}

function fmtDur(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`
}

function fmtClock(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour12: false })
}

/** Duration emphasis tier. NEVER an error tier: red is reserved for severity
 *  (see Console.css). A slow step is not a broken step — kernel starts, env
 *  realizations and remote reads legitimately take tens of seconds, and colouring
 *  those with the error red made healthy work read as failure. Thresholds are
 *  also deliberately high: at 2s virtually every remote step lit up, which trains
 *  the eye to ignore the signal entirely. */
export function durClass(ms: number): string {
  return ms >= 60_000 ? 'is-slow2' : ms >= 10_000 ? 'is-slow1' : ''
}

function SiteChip({ site }: { site: string }) {
  const h = siteHue(site)
  return (
    <span className="crow__site" style={{
      color: `hsl(${h} 55% 42%)`,
      background: `hsl(${h} 55% 45% / .13)`,
      borderColor: `hsl(${h} 55% 50% / .4)`,
    }}>{site}</span>
  )
}

function Row({ r }: { r: ConsoleRow }) {
  const [open, setOpen] = useState(false)
  const f = r.facts
  const title = `${new Date(r.ts).toLocaleString()}${f?.ref ? ` · ${f.ref}` : ''}`
  const showStatus = f?.status && f.status !== 'ok'
  return (
    <div className={`crow crow--${r.severity} ${r.live ? 'crow--live' : ''}`}>
      <div className={`crow__line crow__line--${r.category}`} title={title}
           onClick={() => setOpen(o => !o)}>
        <span className="crow__glyph">{GLYPH[r.category]}</span>
        {r.site && <SiteChip site={r.site} />}
        <span className="crow__verb">{r.verb}</span>
        {r.summary && <span className="crow__sum"> {r.summary}</span>}
        <span className="crow__facts">
          {showStatus && <span className="crow__status"> {f!.status}</span>}
          {f?.bytes != null && ` ${fmtBytes(f.bytes)}`}
          {f?.dur_ms != null && <span className={` crow__dur ${durClass(f.dur_ms)}`}> {fmtDur(f.dur_ms)}</span>}
          {r.live && r.updates ? ` ×${r.updates}` : ''}
        </span>
      </div>
      {open && (
        <div className="crow__detail">
          <div className="crow__detail-head">
            <span>{new Date(r.ts).toLocaleString()}</span>
            <button className="crow__copy" onClick={e => {
              e.stopPropagation()
              navigator.clipboard?.writeText(JSON.stringify(
                { ...r, detail: r.detail }, null, 2)).catch(() => {})
            }}>copy</button>
          </div>
          <pre>{JSON.stringify(r.detail ?? { verb: r.verb, summary: r.summary, facts: r.facts }, null, 2)}</pre>
        </div>
      )}
    </div>
  )
}

export default function Console() {
  const rows = useSyncExternalStore(subscribeConsole, consoleRows)
  const [saved] = useState(loadFacets)
  const [cats, setCats] = useState<Set<Category> | null>(
    saved.cats ? new Set(saved.cats) : null)
  const [sites, setSites] = useState<Set<string> | null>(null)
  const [errorsOnly, setErrorsOnly] = useState(saved.errorsOnly)
  const [q, setQ] = useState('')
  useEffect(() => { saveFacets(cats, errorsOnly) }, [cats, errorsOnly])

  const filter: ConsoleFilter = { cats, sites, errorsOnly, q }
  const shown = useMemo(() => filterRows(rows, filter),
    [rows, cats, sites, errorsOnly, q])
  const clipped = shown.length > RENDER_CAP
  const visible = clipped ? shown.slice(shown.length - RENDER_CAP) : shown
  const knownSites = useMemo(() => consoleSites(rows), [rows])

  // Follow-tail: stick to the bottom unless the user scrolled up; then count
  // arrivals into the "N new" pill instead of yanking the viewport.
  const logRef = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const prevLen = useRef(0)
  const [pending, setPending] = useState(0)
  useEffect(() => {
    const el = logRef.current
    const grew = visible.length - prevLen.current
    prevLen.current = visible.length
    if (!el) return
    if (following.current) {
      el.scrollTop = el.scrollHeight
      setPending(0)
    } else if (grew > 0) {                 // appended rows only — in-place
      setPending(p => p + grew)            // patches don't count as "new"
    }
  }, [visible.length, rows])
  const onScroll = () => {
    const el = logRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 12
    following.current = atBottom
    if (atBottom) setPending(0)
  }

  const toggleCat = (c: Category) => setCats(prev => {
    const next = new Set(prev ?? [])
    if (next.has(c)) next.delete(c); else next.add(c)
    return next.size ? next : null
  })
  const toggleSite = (s: string) => setSites(prev => {
    const next = new Set(prev ?? [])
    if (next.has(s)) next.delete(s); else next.add(s)
    return next.size ? next : null
  })

  // Only categories present in the feed get a chip — 8 chips of which 5 are
  // dead weight would eat the narrow bar for nothing.
  const presentCats = useMemo(() => {
    const have = new Set(rows.map(r => r.category))
    return CATEGORIES.filter(c => have.has(c) || cats?.has(c))
  }, [rows, cats])

  // Divider bookkeeping: insert a faint time rule on >60s gaps.
  const items: React.ReactNode[] = []
  let prevTs: number | null = null
  for (const r of visible) {
    if (prevTs !== null && r.ts - prevTs > GAP_MS) {
      items.push(<div key={`gap-${r.id}`} className="console__gap">— {fmtClock(r.ts)} —</div>)
    }
    prevTs = r.ts
    items.push(<Row key={r.id} r={r} />)
  }

  return (
    <div className="console">
      <div className="console__facets">
        {presentCats.map(c => (
          <button key={c}
            className={`console__chip console__chip--${c} ${cats?.has(c) ? 'is-on' : ''}`}
            onClick={() => toggleCat(c)} title={`filter: ${c}`}>
            {GLYPH[c]} {c}
          </button>
        ))}
        {knownSites.map(s => (
          <button key={`s-${s}`}
            className={`console__chip ${sites?.has(s) ? 'is-on' : ''}`}
            style={{ color: `hsl(${siteHue(s)} 55% 42%)` }}
            onClick={() => toggleSite(s)} title={`filter: site ${s}`}>
            {s}
          </button>
        ))}
        {knownSites.length > 0 && (
          <button className={`console__chip ${sites?.has('') ? 'is-on' : ''}`}
            onClick={() => toggleSite('')} title="filter: no site (local/agent)">
            local
          </button>
        )}
        <button className={`console__chip console__chip--err ${errorsOnly ? 'is-on' : ''}`}
          onClick={() => setErrorsOnly(v => !v)} title="errors + warnings only">
          ⚠
        </button>
        <input className="console__search" placeholder="filter…" value={q}
          onChange={e => setQ(e.target.value)} />
      </div>
      {clipped && (
        <div className="console__clip">showing last {RENDER_CAP} of {shown.length}</div>
      )}
      {visible.length === 0
        ? <div className="drawer__empty">
            {rows.length === 0
              ? 'No activity yet. Run a turn to see agent, compute and data events.'
              : 'Nothing matches the active filters.'}
          </div>
        : (
          <div className="console__log" ref={logRef} onScroll={onScroll}>
            {items}
          </div>
        )}
      {pending > 0 && (
        <button className="console__new" onClick={() => {
          following.current = true
          const el = logRef.current
          if (el) el.scrollTop = el.scrollHeight
          setPending(0)
        }}>{pending} new ↓</button>
      )}
    </div>
  )
}
