/**
 * Drawer — the right-rail (i) panel, now tabbed for observability:
 *   • Context — the structured Manifest the backend assembles each turn
 *     (what the agent sees). The original, default-useful view.
 *   • Console — a live tail of the SSE event stream with a detail-level
 *     selector (Progress / Tools / Debug). A dev/test X-ray; client-side view
 *     over events we already stream (no extra server cost).
 *   • Jobs — background jobs (run_python background=true) and their status.
 *
 * Casual users get immediate-thread progress in the chat; this panel is the
 * deeper view, off the default path. Read-only.
 */
import React, { useEffect, useRef, useState } from 'react'
import type { ManifestSnapshot, LogEntry, JobInfo } from '../types'
import SearchInput from './SearchInput'
import './Drawer.css'

type Tab = 'console' | 'jobs' | 'context'

interface Props {
  manifest: ManifestSnapshot | null
  focusEntityId: string
  threadId: string | null
  eventLog?: LogEntry[]
  jobs?: JobInfo[]
  onClose?: () => void
}

export default function Drawer({ manifest, focusEntityId, threadId, eventLog = [], jobs = [], onClose }: Props) {
  // Remember the last-selected tab per user: devs land on Console/Jobs,
  // everyone else stays on Context (the broadly-useful default).
  const [tab, setTab] = useState<Tab>(() => (localStorage.getItem('aba.drawer.tab') as Tab) || 'console')
  useEffect(() => { localStorage.setItem('aba.drawer.tab', tab) }, [tab])

  const tabs: Tab[] = ['console', 'context', 'jobs']
  const label = (t: Tab) =>
    t === 'console' ? 'Console' : t === 'jobs' ? `Jobs${jobs.length ? ` (${jobs.length})` : ''}` : 'Context'

  return (
    <aside className="drawer">
      <header className="drawer__head">
        <div className="drawer__tabs" role="tablist">
          {tabs.map(t => (
            <button key={t} role="tab" aria-selected={tab === t}
              className={`drawer__tab ${tab === t ? 'is-active' : ''}`} onClick={() => setTab(t)}>
              {label(t)}
            </button>
          ))}
        </div>
        {onClose && <button className="drawer__close" onClick={onClose} title="Close">×</button>}
      </header>
      <div className="drawer__body">
        {tab === 'context' && <ContextTab manifest={manifest} focusEntityId={focusEntityId} threadId={threadId} />}
        {tab === 'console' && <ConsoleTab log={eventLog} />}
        {tab === 'jobs' && <JobsTab jobs={jobs} />}
      </div>
    </aside>
  )
}

// ---------- Context tab — the FULL API context the model received on the most
// recent user-initiated turn (system prompt + tools + history + user text). Sourced
// from guide.py:_dump_turn_context's JSON sidecar in $ABA_TURN_LOG_DIR. ----------
interface TurnContext {
  run_id: string; ts: string; thread_id: string | null; model: string
  focus_entity_id: string | null; tools: string[]; user_text: string; system: string
  history: { role: string; content: unknown }[]
}

function ContextTab({ manifest: liveManifest, focusEntityId, threadId }: {
  manifest: ManifestSnapshot | null; focusEntityId: string; threadId: string | null
}) {
  const [ctx, setCtx] = useState<TurnContext | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [tick, setTick] = useState(0)            // refetch on user click
  const [preview, setPreview] = useState<ManifestSnapshot | null>(null)
  // Search/filter — sits below Meta + applies per-section. Grain varies:
  //   • tools / history → item-level (match → row visible)
  //   • user_text / system / manifest texts → line-level (match → line
  //     visible, leading lines kept as 1-line numeric breadcrumb)
  // Matches highlighted inline; sections auto-expand when q has matches.
  const [q, setQ] = useState('')

  useEffect(() => {
    let cancelled = false
    const url = threadId
      ? `/api/dev/last-turn-context?thread_id=${encodeURIComponent(threadId)}`
      : `/api/dev/last-turn-context`
    fetch(url)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(d => { if (!cancelled) { setCtx(d as TurnContext); setErr(null) } })
      .catch(e => { if (!cancelled) { setCtx(null); setErr(String(e)) } })
    return () => { cancelled = true }
  }, [threadId, tick])

  useEffect(() => {
    let cancelled = false
    const params = new URLSearchParams()
    if (focusEntityId) params.set('focus_entity_id', focusEntityId)
    if (threadId) params.set('thread_id', threadId)
    fetch(`/api/manifest/preview?${params.toString()}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (!cancelled) setPreview((d?.manifest as ManifestSnapshot) ?? null) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [focusEntityId, threadId])
  const m = liveManifest ?? preview

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 10px', fontSize: 11, color: 'var(--text-3)' }}>
        <span>Full API context — most recent turn{threadId ? ' in this thread' : ''}</span>
        <button onClick={() => setTick(t => t + 1)} style={{ marginLeft: 'auto', fontSize: 11, padding: '2px 8px' }}>↻ refresh</button>
      </div>
      {err && !ctx && (
        <div className="drawer__empty">No turn context dumped yet. Send a message — it'll appear here.</div>
      )}
      {ctx && (
        <>
          <Section title="Meta">
            <div className="drawer__kv"><span className="drawer__k">run</span><span className="drawer__v">{ctx.run_id}</span></div>
            <div className="drawer__kv"><span className="drawer__k">ts</span><span className="drawer__v">{ctx.ts}</span></div>
            <div className="drawer__kv"><span className="drawer__k">model</span><span className="drawer__v">{ctx.model}</span></div>
            <div className="drawer__kv"><span className="drawer__k">thread</span><span className="drawer__v">{ctx.thread_id ?? '(default)'}</span></div>
            <div className="drawer__kv"><span className="drawer__k">focus</span><span className="drawer__v">{ctx.focus_entity_id ?? 'workspace'}</span></div>
            <div className="drawer__kv"><span className="drawer__k">system size</span><span className="drawer__v">{ctx.system.length.toLocaleString()} chars</span></div>
            <div className="drawer__kv"><span className="drawer__k">history</span><span className="drawer__v">{ctx.history.length} messages</span></div>
          </Section>
          <div className="ctxsearch-host">
            <SearchInput
              value={q}
              onChange={setQ}
              ariaLabel="Filter context sections"
              placeholder="Filter sections below…"
            />
          </div>
          <FilterableSection title="User message (this turn)"
                             q={q} mode="text" content={ctx.user_text || ''}
                             emptyHint="(empty — resume/Go)" />
          <FilterableSection title="Tools offered"
                             q={q} mode="list" items={ctx.tools} count={ctx.tools.length} />
          <FilterableSection title={`System prompt (${ctx.system.length.toLocaleString()} chars)`}
                             q={q} mode="text" content={ctx.system}
                             maxHeight={500} />
          <FilterableHistorySection title="Message history" q={q} history={ctx.history} />
        </>
      )}
      {m && (
        <details><summary style={{ padding: '4px 10px', fontSize: 12, fontWeight: 600, color: 'var(--text-3)' }}>Manifest (assembled focus/thread context)</summary>
          {m.focus && (
            <div style={{ padding: '4px 10px' }}>
              <div className="drawer__chip">
                <span className="drawer__type">{m.focus.entity_type}</span>
                <span className="drawer__id">{m.focus.entity_id}</span>
              </div>
              <div className="drawer__entity-title">{m.focus.title}</div>
              <pre className="drawer__pre">{m.focus.text}</pre>
            </div>
          )}
          {m.thread?.text && <pre className="drawer__pre" style={{ margin: '4px 10px' }}>{m.thread.text}</pre>}
        </details>
      )}
    </>
  )
}

/** When a matched line is very long (think a 4 KB JSON blob on one
 *  physical line, or one of the system prompt's wide tables), show a
 *  ~200-char window centered on the FIRST match — with leading /
 *  trailing ellipses to mark the cut. Short lines fall through as-is.
 *  PK ask 2026-06-05: "at most 200 chars around each match". */
function excerpt(line: string, q: string, max = 200): string {
  if (!q || line.length <= max) return line
  const lo = line.toLowerCase()
  const hit = lo.indexOf(q.toLowerCase())
  if (hit < 0) return line.slice(0, max) + '…'
  const before = Math.floor((max - q.length) / 2)
  const start = Math.max(0, hit - Math.max(0, before))
  const end = Math.min(line.length, start + max)
  let s = line.slice(start, end)
  if (start > 0) s = '…' + s
  if (end < line.length) s = s + '…'
  return s
}

/** Highlight every case-insensitive substring match of q in s. Empty q
 *  returns s as a single text node. */
function highlight(s: string, q: string): React.ReactNode {
  if (!q) return s
  const ql = q.toLowerCase()
  const out: React.ReactNode[] = []
  let i = 0, k = 0
  while (i < s.length) {
    const hit = s.toLowerCase().indexOf(ql, i)
    if (hit < 0) { out.push(s.slice(i)); break }
    if (hit > i) out.push(s.slice(i, hit))
    out.push(<mark key={k++} className="ctxsearch__hit">{s.slice(hit, hit + q.length)}</mark>)
    i = hit + q.length
  }
  return out
}

/** Line-grain filter for a long text body. Returns the original text when
 *  q is empty; otherwise only lines that contain a match (line numbers
 *  preserved as a left gutter so the user can locate context in the full
 *  document). matchCount = total occurrences, used by section headers. */
function filterTextByLines(text: string, q: string): { rendered: React.ReactNode; matchCount: number } {
  if (!q) return { rendered: text, matchCount: 0 }
  const ql = q.toLowerCase()
  const lines = text.split('\n')
  const kept: { lineNum: number; text: string }[] = []
  let total = 0
  lines.forEach((line, i) => {
    if (line.toLowerCase().includes(ql)) {
      kept.push({ lineNum: i + 1, text: line })
      // Crude per-line occurrence count for the header badge.
      let from = 0
      const lo = line.toLowerCase()
      while (true) {
        const k = lo.indexOf(ql, from); if (k < 0) break
        total++; from = k + ql.length
      }
    }
  })
  if (kept.length === 0) return { rendered: null, matchCount: 0 }
  return {
    matchCount: total,
    rendered: (
      <>
        {kept.map(k => (
          <div key={k.lineNum} className="ctxsearch__line">
            <span className="ctxsearch__lineno">{k.lineNum}</span>
            <span className="ctxsearch__linetxt">{highlight(excerpt(k.text, q), q)}</span>
          </div>
        ))}
      </>
    ),
  }
}

/** A filterable section — wraps content in a <details> with an
 *  auto-expand-on-match behavior + a match counter in the header.
 *  Three modes:
 *    'text' — content is a string; filter line-by-line; highlight matches.
 *    'list' — items are short strings; filter to matching items.
 */
function FilterableSection({ title, q, mode, content, items, count, emptyHint, maxHeight }: {
  title: string
  q: string
  mode: 'text' | 'list'
  content?: string
  items?: string[]
  count?: number
  emptyHint?: string
  maxHeight?: number
}) {
  let body: React.ReactNode = null
  let matchCount = 0
  let hasContent = true
  if (mode === 'text') {
    const text = content ?? ''
    if (!q) {
      body = <pre className="drawer__pre" style={{ margin: '4px 10px', ...(maxHeight ? { maxHeight, overflowY: 'auto' } : {}), whiteSpace: 'pre-wrap' }}>{text || emptyHint || ''}</pre>
      hasContent = !!text || !!emptyHint
    } else {
      const { rendered, matchCount: mc } = filterTextByLines(text, q)
      matchCount = mc
      body = mc > 0
        ? <div className="ctxsearch__lines" style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}>{rendered}</div>
        : <div className="ctxsearch__none">No matches in this section.</div>
    }
  } else {
    const list = items ?? []
    const filtered = q ? list.filter(t => t.toLowerCase().includes(q.toLowerCase())) : list
    matchCount = q ? filtered.length : 0
    if (q && filtered.length === 0) {
      body = <div className="ctxsearch__none">No matches in this section.</div>
    } else {
      body = (
        <div style={{ padding: '4px 10px', fontFamily: 'monospace', fontSize: 11, columnCount: 2, columnGap: 12 }}>
          {filtered.map(t => <div key={t}>{q ? highlight(t, q) : t}</div>)}
        </div>
      )
    }
  }
  // Header text — show "(M of N matches)" when filtering; "(N)" for the list mode default.
  const totalLabel = mode === 'list' ? ` (${count ?? items?.length ?? 0})` : ''
  const matchBadge = q ? (matchCount > 0
      ? <span className="ctxsearch__badge">{matchCount} match{matchCount === 1 ? '' : 'es'}</span>
      : <span className="ctxsearch__badge ctxsearch__badge--none">no match</span>) : null
  // Auto-expand when a query is active AND this section has matches.
  const openWhenFilter = q.length > 0 && matchCount > 0
  return (
    <details open={openWhenFilter || undefined}>
      <summary className="ctxsearch__summary">
        <span className="ctxsearch__title">{title}{totalLabel}</span>
        {matchBadge}
      </summary>
      {hasContent && body}
    </details>
  )
}

/** Filterable Message-history section.
 *
 *  When q is empty → original per-message <details> drilldown (the JSON
 *  is too verbose for an always-on flat view).
 *
 *  When q is non-empty → flat one-line-per-match view, same visual rhythm
 *  as the System-prompt line-filter (numeric gutter, --bg-soft background,
 *  yellow .ctxsearch__hit highlight). A short "[N] role" header marks the
 *  start of each message's matched lines and a hairline divider separates
 *  messages so text-wrapped lines from different messages don't blur
 *  into each other (PK ask, 2026-06-05).
 */
function FilterableHistorySection({ title, q, history }: {
  title: string
  q: string
  history: { role: string; content: unknown }[]
}) {
  const ql = q.toLowerCase()
  // Per-message line-grain scan, collected into groups so we can render
  // a header + hairline between them. matchCount = TOTAL hit lines across
  // the section (drives the header badge).
  type LineHit = { lineNum: number; text: string }
  const groups: { idx: number; role: string; lines: LineHit[] }[] = []
  let totalHits = 0
  if (q) {
    history.forEach((m, i) => {
      const dump = JSON.stringify(m.content, null, 2)
      const ls = dump.split('\n')
      const hits: LineHit[] = []
      ls.forEach((line, n) => {
        if (line.toLowerCase().includes(ql)) hits.push({ lineNum: n + 1, text: line })
      })
      if (hits.length) { groups.push({ idx: i, role: m.role, lines: hits }); totalHits += hits.length }
    })
  }
  const totalLabel = ` (${history.length})`
  const matchBadge = q ? (totalHits > 0
      ? <span className="ctxsearch__badge">{totalHits} match{totalHits === 1 ? '' : 'es'}</span>
      : <span className="ctxsearch__badge ctxsearch__badge--none">no match</span>) : null
  const openWhenFilter = q.length > 0 && totalHits > 0
  return (
    <details open={openWhenFilter || undefined}>
      <summary className="ctxsearch__summary">
        <span className="ctxsearch__title">{title}{totalLabel}</span>
        {matchBadge}
      </summary>
      {q ? (
        totalHits === 0
          ? <div className="ctxsearch__none">No matches in this section.</div>
          : (
            <div className="ctxsearch__lines" style={{ maxHeight: 500, overflowY: 'auto' }}>
              {groups.map((g, gi) => (
                <React.Fragment key={g.idx}>
                  {gi > 0 && <div className="ctxsearch__msgsep" aria-hidden="true" />}
                  <div className="ctxsearch__msghead">
                    [{g.idx.toString().padStart(2, '0')}] <b>{g.role}</b>
                    <span className="ctxsearch__msghead-meta">{g.lines.length} hit{g.lines.length === 1 ? '' : 's'}</span>
                  </div>
                  {g.lines.map(l => (
                    <div key={`${g.idx}-${l.lineNum}`} className="ctxsearch__line">
                      <span className="ctxsearch__lineno">{l.lineNum}</span>
                      <span className="ctxsearch__linetxt">{highlight(excerpt(l.text, q), q)}</span>
                    </div>
                  ))}
                </React.Fragment>
              ))}
            </div>
          )
      ) : (
        <div style={{ padding: '4px 10px' }}>
          {history.map((m, i) => <HistMsg key={i} idx={i} msg={m} />)}
        </div>
      )}
    </details>
  )
}

function HistMsg({ idx, msg, q }: { idx: number; msg: { role: string; content: unknown }; q?: string }) {
  const role = msg.role
  const content = msg.content
  const blocks = Array.isArray(content) ? content
    : typeof content === 'string' ? [{ type: 'text', text: content }]
    : []
  const summary = blocks.length === 0 ? '(empty)' : blocks.map(b => {
    const bb = b as { type?: string; name?: string; text?: string; content?: unknown }
    if (bb.type === 'text') return `text(${(bb.text || '').length})`
    if (bb.type === 'tool_use') return `tool_use ${bb.name}`
    if (bb.type === 'tool_result') return `tool_result(${String(bb.content ?? '').length})`
    return bb.type || '?'
  }).join(' · ')
  const json = JSON.stringify(content, null, 2)
  return (
    <details style={{ marginBottom: 4 }} open={q ? true : undefined}>
      <summary style={{ fontSize: 11, fontFamily: 'monospace', cursor: 'pointer' }}>
        [{idx.toString().padStart(2, '0')}] <b>{role}</b> · {summary}
      </summary>
      <pre style={{ fontSize: 10, lineHeight: 1.3, background: 'var(--tree-active-bg)', padding: 6, borderRadius: 3, marginTop: 4, maxHeight: 300, overflowY: 'auto', whiteSpace: 'pre-wrap' }}>
        {q ? highlight(json, q) : json}
      </pre>
    </details>
  )
}

// ---------- Console tab (SSE event tail + detail level) ----------
const LEVELS: { v: 1 | 2 | 3; label: string }[] = [
  { v: 1, label: 'Progress' }, { v: 2, label: 'Tools' }, { v: 3, label: 'Debug' },
]
function ConsoleTab({ log }: { log: LogEntry[] }) {
  const [level, setLevel] = useState<1 | 2 | 3>(
    () => (Number(localStorage.getItem('aba.console.level')) as 1 | 2 | 3) || 3)
  useEffect(() => { localStorage.setItem('aba.console.level', String(level)) }, [level])
  const endRef = useRef<HTMLDivElement>(null)
  const shown = log.filter(e => e.level <= level)
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }) }, [shown.length])

  return (
    <div className="console">
      <div className="console__bar">
        <span className="console__bar-label">detail</span>
        {LEVELS.map(l => (
          <button key={l.v} className={`console__lvl ${level === l.v ? 'is-active' : ''}`} onClick={() => setLevel(l.v)}>
            {l.label}
          </button>
        ))}
      </div>
      {shown.length === 0
        ? <div className="drawer__empty">No activity yet. Run a turn to see the agent's events.</div>
        : (
          <div className="console__log">
            {shown.map((e, i) => (
              <div key={i} className={`console__row console__row--${e.type}`}>
                <span className="console__t">{fmtTime(e.t)}</span>
                <span className="console__type">{e.type}</span>
                <span className="console__label">{e.label}</span>
              </div>
            ))}
            <div ref={endRef} />
          </div>
        )}
    </div>
  )
}

// ---------- Jobs tab (background jobs) ----------
/** Full job record returned by GET /api/jobs/{id}. Server-shape (snake_case
 *  ISO timestamps) — kept local to JobsTab since nothing else consumes it. */
interface JobDetail {
  id: string
  kind: string
  title: string
  status: string
  params: { code?: string; thread_id?: string | null; project_id?: string | null; run_id?: string | null; timeout_s?: number } | null
  log_tail: string | null
  error: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

function JobsTab({ jobs }: { jobs: JobInfo[] }) {
  // Single-open accordion: at most one job row is expanded at a time. Click
  // the same row to collapse, a different one to switch. The detail panel
  // sits inline directly below its row (no modal — keeps the (i) drawer's
  // density and lets the user scan the list while reading details).
  const [expandedId, setExpandedId] = useState<string | null>(null)
  // Lazy detail cache. Polling /api/jobs (in useChat) keeps the row list
  // fresh but doesn't carry params/log_tail/error — we fetch those once
  // per job on first expansion and cache them here so repeated toggles
  // don't re-hit the network.
  const [details, setDetails] = useState<Record<string, JobDetail>>({})
  const [detailLoading, setDetailLoading] = useState<Record<string, boolean>>({})

  // Re-fetch the detail panel for a running job each poll tick so log_tail
  // updates while the agent watches. Done in the same place that pulls it
  // for the first expansion.
  const fetchDetail = async (id: string, force = false) => {
    if (!force && (details[id] || detailLoading[id])) return
    setDetailLoading(prev => ({ ...prev, [id]: true }))
    try {
      const r = await fetch(`/api/jobs/${encodeURIComponent(id)}`)
      if (!r.ok) return
      const d = await r.json() as JobDetail
      setDetails(prev => ({ ...prev, [id]: d }))
    } catch (_) { /* leave detailLoading set — user can re-toggle to retry */ }
    finally { setDetailLoading(prev => ({ ...prev, [id]: false })) }
  }

  // While a job is expanded AND still running, refresh its detail every 4s
  // so log_tail grows in view. Stops once the row closes or the job ends.
  useEffect(() => {
    if (!expandedId) return
    const row = jobs.find(j => j.id === expandedId)
    if (!row || (row.status !== 'queued' && row.status !== 'running')) return
    const h = window.setInterval(() => { fetchDetail(expandedId, true) }, 4000)
    return () => window.clearInterval(h)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandedId, jobs])

  const toggle = (id: string) => {
    setExpandedId(prev => {
      const next = prev === id ? null : id
      if (next) fetchDetail(next)
      return next
    })
  }

  if (jobs.length === 0) {
    return <div className="drawer__empty">No background jobs. Long pipelines (run_python background) appear here.</div>
  }
  const sorted = [...jobs].sort((a, b) => b.t - a.t)
  return (
    <div className="jobs">
      {sorted.map(j => {
        const open = j.id === expandedId
        const d = details[j.id]
        return (
          <div key={j.id} className={`jobs__row-wrap${open ? ' jobs__row-wrap--open' : ''}`}>
            <button type="button" className="jobs__row" onClick={() => toggle(j.id)}
                    aria-expanded={open} title={open ? 'Click to collapse' : 'Click to see code + output'}>
              <span className={`jobs__caret${open ? ' jobs__caret--open' : ''}`} aria-hidden="true">›</span>
              <span className={`jobs__status jobs__status--${j.status}`}>{j.status}</span>
              <span className="jobs__title">{j.title || j.id}</span>
              <span className="jobs__time">{fmtTime(j.t)}</span>
            </button>
            {open && <JobDetailPanel job={j} detail={d} loading={!!detailLoading[j.id]} />}
          </div>
        )
      })}
    </div>
  )
}

/** Inline detail panel rendered directly under an expanded jobs row. Mirrors
 *  the chat's tool-line "script" + "output" affordances so a background
 *  run feels like its synchronous run_python sibling — same content,
 *  same toggles, different host. */
function JobDetailPanel({ job, detail, loading }: { job: JobInfo; detail: JobDetail | undefined; loading: boolean }) {
  const [showCode, setShowCode] = useState(false)
  // Output pane defaults to open IF there's something to show — surfaces
  // log_tail / error without an extra click. Mirrors the failed-tool
  // behavior in the chat (which auto-reveals the error pane).
  const hasOutput = !!(detail?.log_tail || detail?.error)
  const [showOut, setShowOut] = useState(true)

  if (!detail && loading) {
    return <div className="jobs__detail jobs__detail--loading">loading…</div>
  }
  if (!detail) {
    return <div className="jobs__detail jobs__detail--loading">no detail available</div>
  }

  const code = detail.params?.code || ''
  const out = detail.log_tail || ''
  const err = detail.error || ''
  const duration = durationFmt(detail.started_at, detail.finished_at)
  return (
    <div className="jobs__detail">
      <div className="jobs__detail-meta">
        <span>id <code className="jobs__mono">{detail.id}</code></span>
        {detail.params?.thread_id && (
          <span>thread <code className="jobs__mono">{detail.params.thread_id}</code></span>
        )}
        {duration && <span>duration {duration}</span>}
        {detail.started_at && <span title={detail.started_at}>started {fmtTimeStr(detail.started_at)}</span>}
        {detail.finished_at && <span title={detail.finished_at}>finished {fmtTimeStr(detail.finished_at)}</span>}
      </div>
      {err && (
        <div className="jobs__error">
          <div className="jobs__detail-label">error</div>
          <pre className="jobs__pre jobs__pre--err">{err}</pre>
        </div>
      )}
      {code && (
        <div className="jobs__section">
          <button type="button" className="jobs__toggle" onClick={() => setShowCode(s => !s)}>
            {showCode ? '▾ code' : '▸ code'}
            <span className="jobs__toggle-meta">{code.length} chars</span>
          </button>
          {showCode && <pre className="jobs__pre jobs__pre--code">{code}</pre>}
        </div>
      )}
      {hasOutput && (
        <div className="jobs__section">
          <button type="button" className="jobs__toggle" onClick={() => setShowOut(s => !s)}>
            {showOut ? '▾ output' : '▸ output'}
            <span className="jobs__toggle-meta">{(out || err).length} chars</span>
          </button>
          {showOut && out && <pre className="jobs__pre">{out}</pre>}
        </div>
      )}
      {!code && !out && !err && (
        <div className="jobs__detail-hint">(no captured input/output — this job may not have been a run_python)</div>
      )}
    </div>
  )
}

function _pad(n: number): string { return n < 10 ? '0' + n : String(n) }

/** "MM-DD HH:MM:SS" — date + time, no ms, no year (assumed current).
 *  Used for the row-level timestamp on the Jobs panel + the detail
 *  panel's started/finished pills. Single format keeps the column
 *  alignment predictable. */
function fmtTime(t: number): string {
  if (!t) return ''
  const d = new Date(t)
  return `${_pad(d.getMonth() + 1)}-${_pad(d.getDate())} `
       + `${_pad(d.getHours())}:${_pad(d.getMinutes())}:${_pad(d.getSeconds())}`
}

function fmtTimeStr(iso: string): string {
  if (!iso) return ''
  try { return fmtTime(Date.parse(iso)) } catch { return iso }
}

/** "32s" / "2m 14s" — readable elapsed between start and finish. */
function durationFmt(startIso: string | null, finishIso: string | null): string {
  if (!startIso) return ''
  const start = Date.parse(startIso); if (Number.isNaN(start)) return ''
  const end = finishIso ? Date.parse(finishIso) : Date.now()
  if (Number.isNaN(end)) return ''
  const s = Math.max(0, Math.round((end - start) / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60), r = s % 60
  return `${m}m ${r.toString().padStart(2, '0')}s`
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="drawer__section">
      <div className="drawer__section-title">{title}</div>
      <div className="drawer__section-body">{children}</div>
    </section>
  )
}
