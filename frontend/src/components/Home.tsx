/**
 * Home screen (Phase 13). Adapts to project state:
 *  - empty (no datasets): welcome + entry cards (Upload / Try a sample)
 *  - populated: project mini-dashboard + recent activity + attention panel
 *
 * Multi-project switching is a follow-on; today there is one implicit
 * project (the workspace).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import './Home.css'

interface Summary {
  project_title: string
  counts: Record<string, number>
  n_datasets: number
  started_at: string | null
  last_touched: string | null
  recent_events: { id: number; kind: string; entity_id: string | null; title: string | null; ts: string; detail: Record<string, unknown> | null }[]
  attention: {
    pending_suggestions: number
    active_jobs: number
    failed_jobs: number
    advisor_notes: number
  }
}

interface Props {
  onOpenWorkspace: () => void
  onEntitiesChanged: () => void
  onFocus: (id: string) => void
}

const EVENT_LABEL: Record<string, string> = {
  entity_created: 'created',
  scenario_created: 'scenario',
  advisor_note: 'advisor note',
  suggestion_logged: 'suggestion',
}

const COUNT_ORDER = ['dataset', 'figure', 'table', 'result', 'finding', 'claim']

export default function Home({ onOpenWorkspace, onEntitiesChanged, onFocus }: Props) {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    try {
      const r = await fetch('/api/home-summary')
      if (r.ok) setSummary(await r.json())
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  async function uploadFile(f: File) {
    setBusy(true)
    try {
      const form = new FormData(); form.append('file', f)
      const r = await fetch('/api/upload', { method: 'POST', body: form })
      if (r.ok) {
        const ds = await r.json()
        onEntitiesChanged(); onFocus(ds.id); onOpenWorkspace()
      }
    } finally { setBusy(false) }
  }

  async function trySample() {
    setBusy(true)
    try {
      const r = await fetch('/api/sample-project', { method: 'POST' })
      if (r.ok) {
        const ds = await r.json()
        onEntitiesChanged(); onFocus(ds.id); onOpenWorkspace()
      }
    } finally { setBusy(false) }
  }

  const empty = (summary?.n_datasets ?? 0) === 0

  return (
    <div className="home">
      <input ref={fileRef} type="file" style={{ display: 'none' }}
             onChange={e => { const f = e.target.files?.[0]; if (f) uploadFile(f) }} />

      {empty ? (
        <div className="home__empty">
          <h1 className="home__welcome">Welcome to ABA</h1>
          <p className="home__sub">An AI-orchestrated workspace for biology projects.</p>
          <div className="home__cards">
            <button className="home__card" disabled={busy} onClick={() => fileRef.current?.click()}>
              <div className="home__card-icon">⬆</div>
              <div className="home__card-title">Upload data</div>
              <div className="home__card-desc">CSV, h5ad, or a 10x archive</div>
            </button>
            <button className="home__card" disabled={busy} onClick={trySample}>
              <div className="home__card-icon">✦</div>
              <div className="home__card-title">Try a sample</div>
              <div className="home__card-desc">A small per-cell QC dataset to explore</div>
            </button>
            <button className="home__card" onClick={onOpenWorkspace}>
              <div className="home__card-icon">→</div>
              <div className="home__card-title">Open workspace</div>
              <div className="home__card-desc">Talk to Guide about your project</div>
            </button>
          </div>
          <p className="home__what">
            ABA follows what you're looking at — click any figure or result and the
            agent picks up its full context. Your analyses, results, and claims build
            into a connected project, not a scroll of chat.
          </p>
        </div>
      ) : (
        <div className="home__dash">
          <div className="home__dash-head">
            <h1>{summary?.project_title || 'Workspace'}</h1>
            <button className="home__open" onClick={onOpenWorkspace}>Open workspace →</button>
          </div>
          <div className="home__grid">
            <div className="home__panel home__panel--stats">
              <div className="home__panel-head">Project</div>
              <div className="home__stats">
                {COUNT_ORDER.filter(t => summary?.counts[t]).map(t => (
                  <div key={t} className="home__stat">
                    <span className="home__stat-n">{summary?.counts[t]}</span>
                    <span className="home__stat-t">{t}{(summary?.counts[t] ?? 0) > 1 ? 's' : ''}</span>
                  </div>
                ))}
                {Object.keys(summary?.counts ?? {}).length === 0 && (
                  <div className="home__muted">No entities yet.</div>
                )}
              </div>
              {summary?.last_touched && (
                <div className="home__muted">
                  Last touched {new Date(summary.last_touched).toLocaleString()}
                </div>
              )}
              <div className="home__dash-actions">
                <button onClick={() => fileRef.current?.click()} disabled={busy}>+ Add data</button>
              </div>
            </div>

            <div className="home__panel home__panel--attention">
              <div className="home__panel-head">Attention</div>
              <AttentionRow n={summary?.attention.advisor_notes ?? 0} label="advisor notes" />
              <AttentionRow n={summary?.attention.pending_suggestions ?? 0} label="context suggestions" />
              <AttentionRow n={summary?.attention.active_jobs ?? 0} label="running jobs" />
              <AttentionRow n={summary?.attention.failed_jobs ?? 0} label="failed jobs" danger />
              {(summary?.attention.advisor_notes ?? 0) === 0 &&
               (summary?.attention.pending_suggestions ?? 0) === 0 &&
               (summary?.attention.active_jobs ?? 0) === 0 &&
               (summary?.attention.failed_jobs ?? 0) === 0 && (
                 <div className="home__muted">Nothing needs your attention.</div>
               )}
            </div>
          </div>

          <div className="home__panel">
            <div className="home__panel-head">Recent activity</div>
            {(summary?.recent_events.length ?? 0) === 0 ? (
              <div className="home__muted">No activity yet.</div>
            ) : (
              <div className="home__events">
                {summary?.recent_events.map(ev => (
                  <button key={ev.id} className="home__event"
                          onClick={() => {
                            if (ev.entity_id) onFocus(ev.entity_id)
                            onOpenWorkspace()
                          }}>
                    <span className="home__event-kind">{EVENT_LABEL[ev.kind] ?? ev.kind}</span>
                    <span className="home__event-title">{ev.title ?? ''}</span>
                    <span className="home__event-date">{rel(ev.ts)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function AttentionRow({ n, label, danger }: { n: number; label: string; danger?: boolean }) {
  return (
    <div className={`home__attn ${n > 0 ? 'is-on' : ''} ${danger && n > 0 ? 'is-danger' : ''}`}>
      <span className="home__attn-dot" />
      <span className="home__attn-n">{n}</span>
      <span className="home__attn-label">{label}</span>
    </div>
  )
}

function rel(ts: string): string {
  const d = (Date.now() - new Date(ts).getTime()) / 1000
  if (d < 60) return 'just now'
  if (d < 3600) return `${Math.floor(d / 60)}m ago`
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`
  return new Date(ts).toLocaleDateString()
}
