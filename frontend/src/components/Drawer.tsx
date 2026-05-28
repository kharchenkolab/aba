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

  const tabs: Tab[] = ['console', 'jobs', 'context']
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

// ---------- Context tab (the original manifest view) ----------
function ContextTab({ manifest: liveManifest, focusEntityId, threadId }: {
  manifest: ManifestSnapshot | null; focusEntityId: string; threadId: string | null
}) {
  const [preview, setPreview] = useState<ManifestSnapshot | null>(null)
  useEffect(() => {
    let cancelled = false
    const params = new URLSearchParams()
    if (focusEntityId) params.set('focus_entity_id', focusEntityId)
    if (threadId) params.set('thread_id', threadId)
    fetch(`/api/manifest/preview?${params.toString()}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (!cancelled) setPreview((d?.manifest as ManifestSnapshot) ?? null) })
      .catch(() => { /* drawer just shows empty */ })
    return () => { cancelled = true }
  }, [focusEntityId, threadId])

  const m = liveManifest ?? preview
  if (!m) return <div className="drawer__empty">The agent's loaded context will appear here once a turn runs.</div>
  return (
    <>
      <Section title="Focus">
        {m.focus ? (
          <>
            <div className="drawer__chip">
              <span className="drawer__type">{m.focus.entity_type}</span>
              <span className="drawer__id">{m.focus.entity_id}</span>
            </div>
            <div className="drawer__entity-title">{m.focus.title}</div>
            {m.focus.fields_loaded.length > 0 && (
              <div className="drawer__fields">
                {m.focus.fields_loaded.map(f => <span key={f} className="drawer__field">{f}</span>)}
              </div>
            )}
            <pre className="drawer__pre">{m.focus.text}</pre>
          </>
        ) : <div className="drawer__none">No entity focused — workspace scope.</div>}
      </Section>
      <Section title="Thread context">
        {m.thread?.text ? <pre className="drawer__pre">{m.thread.text}</pre>
          : <div className="drawer__none">Nothing kept in this thread yet.</div>}
      </Section>
      {m.policy_text && <Section title="Adaptive policy"><pre className="drawer__pre">{m.policy_text}</pre></Section>}
      <Section title="Meta">
        <div className="drawer__kv"><span className="drawer__k">session</span><span className="drawer__v">{m.session_id}</span></div>
        <div className="drawer__kv"><span className="drawer__k">turn</span><span className="drawer__v">{m.turn_index}</span></div>
      </Section>
    </>
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
function JobsTab({ jobs }: { jobs: JobInfo[] }) {
  if (jobs.length === 0) {
    return <div className="drawer__empty">No background jobs. Long pipelines (run_python background) appear here.</div>
  }
  const sorted = [...jobs].sort((a, b) => b.t - a.t)
  return (
    <div className="jobs">
      {sorted.map(j => (
        <div key={j.id} className="jobs__row">
          <span className={`jobs__status jobs__status--${j.status}`}>{j.status}</span>
          <span className="jobs__title">{j.title || j.id}</span>
          <span className="jobs__time">{fmtTime(j.t)}</span>
        </div>
      ))}
    </div>
  )
}

function fmtTime(t: number): string {
  const d = new Date(t)
  return d.toTimeString().slice(0, 8)
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="drawer__section">
      <div className="drawer__section-title">{title}</div>
      <div className="drawer__section-body">{children}</div>
    </section>
  )
}
