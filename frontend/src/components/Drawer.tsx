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
import { useEffect, useRef, useState } from 'react'
import type { ManifestSnapshot, LogEntry, JobInfo } from '../types'
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
          <details><summary style={{ padding: '4px 10px', fontSize: 12, fontWeight: 600 }}>User message (this turn)</summary>
            <pre className="drawer__pre" style={{ margin: '4px 10px' }}>{ctx.user_text || '(empty — resume/Go)'}</pre>
          </details>
          <details><summary style={{ padding: '4px 10px', fontSize: 12, fontWeight: 600 }}>Tools offered ({ctx.tools.length})</summary>
            <div style={{ padding: '4px 10px', fontFamily: 'monospace', fontSize: 11, columnCount: 2, columnGap: 12 }}>
              {ctx.tools.map(t => <div key={t}>{t}</div>)}
            </div>
          </details>
          <details><summary style={{ padding: '4px 10px', fontSize: 12, fontWeight: 600 }}>System prompt ({ctx.system.length.toLocaleString()} chars)</summary>
            <pre className="drawer__pre" style={{ margin: '4px 10px', maxHeight: 500, overflowY: 'auto', whiteSpace: 'pre-wrap' }}>{ctx.system}</pre>
          </details>
          <details><summary style={{ padding: '4px 10px', fontSize: 12, fontWeight: 600 }}>Message history ({ctx.history.length})</summary>
            <div style={{ padding: '4px 10px' }}>
              {ctx.history.map((m, i) => <HistMsg key={i} idx={i} msg={m} />)}
            </div>
          </details>
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

function HistMsg({ idx, msg }: { idx: number; msg: { role: string; content: unknown } }) {
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
  return (
    <details style={{ marginBottom: 4 }}>
      <summary style={{ fontSize: 11, fontFamily: 'monospace', cursor: 'pointer' }}>
        [{idx.toString().padStart(2, '0')}] <b>{role}</b> · {summary}
      </summary>
      <pre style={{ fontSize: 10, lineHeight: 1.3, background: 'var(--tree-active-bg)', padding: 6, borderRadius: 3, marginTop: 4, maxHeight: 300, overflowY: 'auto', whiteSpace: 'pre-wrap' }}>
        {JSON.stringify(content, null, 2)}
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

function fmtTime(t: number): string {
  const d = new Date(t)
  return d.toTimeString().slice(0, 8)
}

function fmtTimeStr(iso: string): string {
  try { return new Date(iso).toTimeString().slice(0, 8) } catch { return iso }
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
