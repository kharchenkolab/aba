/**
 * Home — the project hub.
 *
 *  - Zero projects: a three-card start screen (New / Sample / Upload).
 *  - One or more: a two-column layout — the current project's detail
 *    dashboard (counts, attention, recent activity) in the main column, and a
 *    searchable list of the *other* projects in a side column (hidden when
 *    there are none).
 *
 * Creating goes through a naming modal (no surprise auto-named projects);
 * deleting goes through a full confirmation modal reached from a ⋯ menu.
 */
import { useCallback, useEffect, useState } from 'react'
import './Home.css'

interface Project {
  id: string
  name: string
  created_at: string
  last_touched: string
  current: boolean
  counts: Record<string, number>
}

interface Summary {
  project_title: string
  counts: Record<string, number>
  n_datasets: number
  last_touched: string | null
  recent_events: { id: number; kind: string; entity_id: string | null; title: string | null; ts: string }[]
  attention: { pending_suggestions: number; active_jobs: number; failed_jobs: number; advisor_notes: number }
}

interface Props {
  /** Reload entities + chat for the (now-active) project and enter its view. */
  onEnter: () => void
  /** Notify the shell that the project set changed (so it can re-gate nav). */
  onProjectsChanged?: () => void
}

type Modal =
  | { kind: 'create'; name: string; file: File | null }
  | { kind: 'rename'; pid: string; name: string }
  | { kind: 'delete'; pid: string; name: string }
  | null

const COUNT_ORDER = ['dataset', 'figure', 'table', 'result', 'finding', 'claim']
const CARD_ORDER = ['thread', 'claim', 'figure', 'dataset']  // shown on project cards
const EVENT_LABEL: Record<string, string> = {
  entity_created: 'created', scenario_created: 'scenario',
  advisor_note: 'advisor note', suggestion_logged: 'suggestion',
}

function baseName(fn: string): string {
  return fn.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim() || 'Untitled project'
}

export default function Home({ onEnter, onProjectsChanged }: Props) {
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [busy, setBusy] = useState(false)
  const [modal, setModal] = useState<Modal>(null)
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  const load = useCallback(async () => {
    try {
      const [pr, sr] = await Promise.all([fetch('/api/projects'), fetch('/api/home-summary')])
      if (pr.ok) setProjects(await pr.json())
      if (sr.ok) setSummary(await sr.json())
    } catch { /* ignore */ }
    onProjectsChanged?.()
  }, [onProjectsChanged])
  useEffect(() => { load() }, [load])

  async function open(id: string) {
    setBusy(true)
    try {
      await fetch(`/api/projects/${encodeURIComponent(id)}/open`, { method: 'POST' })
      onEnter()
    } finally { setBusy(false) }
  }
  async function submitCreate(name: string, file: File | null) {
    setBusy(true)
    try {
      await fetch('/api/projects', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim() || 'Untitled project' }),
      })
      if (file) { const f = new FormData(); f.append('file', file); await fetch('/api/upload', { method: 'POST', body: f }) }
      setModal(null); onEnter()
    } finally { setBusy(false) }
  }
  async function trySample() {
    setBusy(true)
    try {
      await fetch('/api/projects', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'Sample project' }),
      })
      await fetch('/api/sample-project', { method: 'POST' })
      onEnter()
    } finally { setBusy(false) }
  }
  async function submitRename(pid: string, name: string) {
    setBusy(true)
    try {
      await fetch(`/api/projects/${encodeURIComponent(pid)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim() }),
      })
      setModal(null); await load()
    } finally { setBusy(false) }
  }
  async function confirmDelete(pid: string) {
    setBusy(true)
    try {
      await fetch(`/api/projects/${encodeURIComponent(pid)}`, { method: 'DELETE' })
      setModal(null); await load()
    } finally { setBusy(false) }
  }

  const list = projects ?? []
  const startCreate = () => setModal({ kind: 'create', name: 'Untitled project', file: null })
  const current = list.find(p => p.current) ?? null
  // Most-recent first, so the top cards surface the projects you're likely to
  // jump back to.
  const others = list.filter(p => !p.current)
    .sort((a, b) => (b.last_touched || '').localeCompare(a.last_touched || ''))
  const q = query.trim().toLowerCase()
  const matchOthers = q ? others.filter(p => p.name.toLowerCase().includes(q)) : others
  // No query: up to 3 rich cards on top, the rest as a compact list below.
  const cards = matchOthers.slice(0, 3)
  const rest = matchOthers.slice(3)

  const menu = (p: Project) => (
    <>
      <button className="home__proj-menu" title="Project actions"
              onClick={e => { e.stopPropagation(); setMenuFor(menuFor === p.id ? null : p.id) }}>⋯</button>
      {menuFor === p.id && (
        <div className="home__menu" onClick={e => e.stopPropagation()}>
          <button onClick={() => { setMenuFor(null); setModal({ kind: 'rename', pid: p.id, name: p.name }) }}>Rename</button>
          <button className="home__menu-danger"
                  onClick={() => { setMenuFor(null); setModal({ kind: 'delete', pid: p.id, name: p.name }) }}>Delete project</button>
        </div>
      )}
    </>
  )

  return (
    <div className="home" onClick={() => setMenuFor(null)}>
      {list.length === 0 ? (
        <div className="home__empty">
          <h1 className="home__welcome">Welcome to ABA</h1>
          <p className="home__sub">An AI-orchestrated workspace for biology projects.</p>
          <div className="home__cards">
            <button className="home__card" disabled={busy} onClick={startCreate}>
              <div className="home__card-icon">+</div>
              <div className="home__card-title">Start a project</div>
              <div className="home__card-desc">Name it, then talk to Guide about your data</div>
            </button>
            <button className="home__card" disabled={busy} onClick={trySample}>
              <div className="home__card-icon">✦</div>
              <div className="home__card-title">Try a sample</div>
              <div className="home__card-desc">A small per-cell QC dataset to explore</div>
            </button>
            <UploadCard busy={busy} onFile={f => setModal({ kind: 'create', name: baseName(f.name), file: f })} />
          </div>
          <p className="home__what">
            Each project is its own workspace — datasets, analyses, figures, and
            the findings and claims they build into.
          </p>
        </div>
      ) : (
        <div className="home__hub">
          <div className="home__bar">
            <span className="home__bar-label">{others.length > 0 ? `${list.length} projects` : ''}</span>
            <div className="home__hub-actions">
              <button className="home__btn" disabled={busy} onClick={trySample}>✦ Try a sample</button>
              <button className="home__btn home__btn--primary" disabled={busy} onClick={startCreate}>+ New project</button>
            </div>
          </div>

          <div className={`home__cols ${others.length > 0 ? 'has-side' : ''}`}>
            {/* Main column — current project detail */}
            <div className="home__main">
              <div className="home__cur-head">
                <div className="home__cur-titles">
                  <h1>{current?.name ?? summary?.project_title ?? 'Project'}</h1>
                  {summary?.last_touched && (
                    <span className="home__muted">Last touched {rel(summary.last_touched)}</span>
                  )}
                </div>
                <div className="home__cur-actions">
                  {current && menu(current)}
                  <button className="home__btn home__btn--primary" disabled={busy} onClick={onEnter}>Open project →</button>
                </div>
              </div>

              <div className="home__panel">
                <div className="home__panel-head">Project</div>
                <div className="home__stats">
                  {COUNT_ORDER.filter(t => summary?.counts[t]).map(t => (
                    <div key={t} className="home__stat">
                      <span className="home__stat-n">{summary?.counts[t]}</span>
                      <span className="home__stat-t">{t}{(summary?.counts[t] ?? 0) > 1 ? 's' : ''}</span>
                    </div>
                  ))}
                  {Object.keys(summary?.counts ?? {}).length === 0 && (
                    <div className="home__muted">Empty — open the project to add data.</div>
                  )}
                </div>
              </div>

              <div className="home__panel">
                <div className="home__panel-head">Attention</div>
                <AttentionRow n={summary?.attention.advisor_notes ?? 0} label="advisor notes" />
                <AttentionRow n={summary?.attention.pending_suggestions ?? 0} label="context suggestions" />
                <AttentionRow n={summary?.attention.active_jobs ?? 0} label="running jobs" />
                <AttentionRow n={summary?.attention.failed_jobs ?? 0} label="failed jobs" danger />
                {!summary?.attention.advisor_notes && !summary?.attention.pending_suggestions &&
                 !summary?.attention.active_jobs && !summary?.attention.failed_jobs && (
                  <div className="home__muted">Nothing needs your attention.</div>
                )}
              </div>

              <div className="home__panel">
                <div className="home__panel-head">Recent activity</div>
                {(summary?.recent_events.length ?? 0) === 0 ? (
                  <div className="home__muted">No activity yet.</div>
                ) : (
                  <div className="home__events">
                    {summary?.recent_events.map(ev => (
                      <button key={ev.id} className="home__event" onClick={onEnter}>
                        <span className="home__event-kind">{EVENT_LABEL[ev.kind] ?? ev.kind}</span>
                        <span className="home__event-title">{ev.title ?? ''}</span>
                        <span className="home__event-date">{rel(ev.ts)}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Side column — other projects (only when there are any) */}
            {others.length > 0 && (
              <aside className="home__side">
                {others.length > 3 && (
                  <input className="home__side-search" placeholder="Filter projects…"
                         value={query} onChange={e => setQuery(e.target.value)} />
                )}
                {matchOthers.length === 0 ? (
                  <div className="home__muted">No matches.</div>
                ) : (
                  <>
                    {/* Up to 3 rich cards */}
                    <div className="home__pcards">
                      {cards.map(p => {
                        const stats = CARD_ORDER.filter(t => p.counts[t])
                        return (
                          <div key={p.id} className="home__pcard" role="button" onClick={() => open(p.id)}>
                            <div className="home__pcard-head">
                              <span className="home__pcard-name">{p.name}</span>
                              {menu(p)}
                            </div>
                            <div className="home__pcard-stats">
                              {stats.length ? stats.map(t => (
                                <span key={t} className="home__pcard-stat">
                                  <b>{p.counts[t]}</b> {t}{p.counts[t] > 1 ? 's' : ''}
                                </span>
                              )) : <span className="home__muted">empty</span>}
                            </div>
                            <div className="home__pcard-foot">Last touched {rel(p.last_touched)}</div>
                          </div>
                        )
                      })}
                    </div>
                    {/* The rest as a compact list */}
                    {rest.length > 0 && (
                      <>
                        <div className="home__side-subhead">Other projects</div>
                        <div className="home__side-list">
                          {rest.map(p => {
                            const counts = CARD_ORDER.filter(t => p.counts[t]).map(t => `${p.counts[t]} ${t.charAt(0)}`)
                            return (
                              <div key={p.id} className="home__side-item" role="button" onClick={() => open(p.id)}>
                                <div className="home__side-item-head">
                                  <span className="home__side-item-name">{p.name}</span>
                                  {menu(p)}
                                </div>
                                <div className="home__side-item-meta">
                                  {counts.length ? counts.join(' · ') : 'empty'} · {rel(p.last_touched)}
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      </>
                    )}
                  </>
                )}
              </aside>
            )}
          </div>
        </div>
      )}

      {modal?.kind === 'create' && (
        <NameModal title="New project" cta="Create" busy={busy} initial={modal.name}
                   hint={modal.file ? `Will upload “${modal.file.name}” into the project.` : undefined}
                   onCancel={() => setModal(null)} onSubmit={name => submitCreate(name, modal.file)} />
      )}
      {modal?.kind === 'rename' && (
        <NameModal title="Rename project" cta="Save" busy={busy} initial={modal.name}
                   onCancel={() => setModal(null)} onSubmit={name => submitRename(modal.pid, name)} />
      )}
      {modal?.kind === 'delete' && (
        <div className="modal-backdrop" onClick={() => setModal(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2 className="modal__title">Delete “{modal.name}”?</h2>
            <p className="modal__body">This permanently removes the project and all of its
              datasets, analyses, figures, and findings. This can’t be undone.</p>
            <div className="modal__actions">
              <button className="home__btn" disabled={busy} onClick={() => setModal(null)}>Cancel</button>
              <button className="home__btn home__btn--danger" disabled={busy}
                      onClick={() => confirmDelete(modal.pid)}>Delete project</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function UploadCard({ busy, onFile }: { busy: boolean; onFile: (f: File) => void }) {
  let ref: HTMLInputElement | null = null
  return (
    <button className="home__card" disabled={busy} onClick={() => ref?.click()}>
      <input type="file" style={{ display: 'none' }} ref={el => { ref = el }}
             onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f) }} />
      <div className="home__card-icon">⬆</div>
      <div className="home__card-title">Upload data</div>
      <div className="home__card-desc">Start a project from a CSV, h5ad, or 10x archive</div>
    </button>
  )
}

function NameModal({ title, cta, initial, hint, busy, onCancel, onSubmit }: {
  title: string; cta: string; initial: string; hint?: string; busy: boolean
  onCancel: () => void; onSubmit: (name: string) => void
}) {
  const [name, setName] = useState(initial)
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <h2 className="modal__title">{title}</h2>
        <input className="modal__input" autoFocus value={name}
               onChange={e => setName(e.target.value)} onFocus={e => e.target.select()}
               onKeyDown={e => { if (e.key === 'Enter' && name.trim()) onSubmit(name) }} />
        {hint && <p className="modal__hint">{hint}</p>}
        <div className="modal__actions">
          <button className="home__btn" disabled={busy} onClick={onCancel}>Cancel</button>
          <button className="home__btn home__btn--primary" disabled={busy || !name.trim()}
                  onClick={() => onSubmit(name)}>{cta}</button>
        </div>
      </div>
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
