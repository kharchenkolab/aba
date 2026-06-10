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
// Home stat tiles dispatch through the bio home-tile registry — the
// shell asks for the tile list, bio decides the labels + count rules
// (which entity types roll up into "results", "runs", etc.).
import { home_tiles, card_order } from '../bio/homeTiles'
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
  onEnter: (pid: string) => void
  /** Notify the shell that the project set changed (so it can re-gate nav). */
  onProjectsChanged?: () => void
}

type Modal =
  | { kind: 'create'; name: string; file: File | null }
  | { kind: 'rename'; pid: string; name: string }
  | { kind: 'delete'; pid: string; name: string }
  | null

// Bio decides which entity types surface as the side-card meta line.
const EVENT_LABEL: Record<string, string> = {
  entity_created: 'created', scenario_created: 'scenario',
  advisor_note: 'advisor note', suggestion_logged: 'suggestion',
}

function baseName(fn: string): string {
  return fn.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim() || 'Untitled project'
}

export default function Home({ onEnter, onProjectsChanged }: Props) {
  const [projects, setProjects] = useState<Project[] | null>(null)
  // The project currently PREVIEWED in the central column. UI-only — selecting
  // a project here doesn't touch backend state (the server's "current project"
  // only changes when the user actually enters one via onEnter). PK 2026-06-03:
  // clicking a card in the right rail used to /open the project server-side,
  // which both was a wrong abstraction and made post-restart state confusing.
  // Defaults to null; the effect below seeds it to the most-recently-touched
  // project once projects load.
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [busy, setBusy] = useState(false)
  const [modal, setModal] = useState<Modal>(null)
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  // Per-project recovery-report cache (I5). Populated lazily for any project
  // whose dir contains recovery_report.json — i.e. projects that came in via
  // aba-recover and may have missing deps the admin should know about.
  type RecoveryReport = {
    pid: string
    missing: { entity_types: string[]; recipes: string[]; capabilities: string[]; tools: string[] }
    source: { aba_commit: string | null; aba_version: string | null }
    host: { aba_commit: string | null; aba_version: string | null }
  }
  const [recoveryReports, setRecoveryReports] = useState<Record<string, RecoveryReport | null>>({})
  const [verifyResult, setVerifyResult] = useState<{ pid: string; rep: any } | null>(null)

  const load = useCallback(async () => {
    // Initial-load: just fetch projects. Summary is fetched per-selection
    // below (useEffect on selectedId/list[0]), so the preview matches.
    try {
      const pr = await fetch('/api/projects')
      if (pr.ok) setProjects(await pr.json())
    } catch { /* ignore */ }
    onProjectsChanged?.()
  }, [onProjectsChanged])
  useEffect(() => { load() }, [load])

  // I5 — fan out one recovery-report fetch per project. 200 OK with `null`
  // body means "no recovery report" (project wasn't imported, or no missing
  // deps); only banners + the verify modal care about non-null payloads.
  useEffect(() => {
    if (!projects || projects.length === 0) return
    let cancelled = false
    Promise.all(projects.map(p =>
      fetch(`/api/projects/${encodeURIComponent(p.id)}/recovery-report`)
        .then(r => r.ok ? r.json() : null)
        .then(j => [p.id, j] as [string, any])
        .catch(() => [p.id, null] as [string, any])
    )).then(pairs => {
      if (cancelled) return
      const next: Record<string, any> = {}
      for (const [pid, j] of pairs) next[pid] = j
      setRecoveryReports(next)
    })
    return () => { cancelled = true }
  }, [projects])
  function hasMissingDeps(pid: string): boolean {
    const rep = recoveryReports[pid]
    if (!rep || !rep.missing) return false
    const m = rep.missing
    return (m.entity_types?.length || 0) + (m.recipes?.length || 0)
         + (m.capabilities?.length || 0) + (m.tools?.length || 0) > 0
  }
  async function verifyRecovery(pid: string) {
    setBusy(true)
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(pid)}/verify-recovery?depth=full`,
                            { method: 'POST' })
      const j = await r.json()
      setVerifyResult({ pid, rep: j })
    } finally { setBusy(false) }
  }

  // Right-rail click: pure UI selection — just preview this project in the
  // central column. No backend call; the server's current-project state
  // changes only when the user actually enters via onEnter.
  function selectProject(id: string) {
    setSelectedId(id)
    setMenuFor(null)
  }
  async function submitCreate(name: string, file: File | null) {
    setBusy(true)
    try {
      const r = await fetch('/api/projects', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim() || 'Untitled project' }),
      })
      const created = await r.json() as { id: string }
      if (file) { const f = new FormData(); f.append('file', file); await fetch('/api/upload', { method: 'POST', body: f }) }
      setModal(null); onEnter(created.id)
    } finally { setBusy(false) }
  }
  async function trySample() {
    setBusy(true)
    try {
      const r = await fetch('/api/projects', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'Sample project' }),
      })
      const created = await r.json() as { id: string }
      await fetch('/api/sample-project', { method: 'POST' })
      onEnter(created.id)
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

  // Sort by most-recently touched (descending) so the project you used last
  // is at the top of the side rail. Stable: fall back to id for equal mtimes.
  const list = [...(projects ?? [])].sort((a, b) => {
    const da = a.last_touched || a.created_at || ''
    const db = b.last_touched || b.created_at || ''
    if (da === db) return a.id.localeCompare(b.id)
    return da < db ? 1 : -1
  })
  const startCreate = () => setModal({ kind: 'create', name: 'Untitled project', file: null })
  // Central-column preview: explicit user selection wins; otherwise default to
  // the most-recently-touched project (= list[0] after the sort above). Keeps
  // the page meaningful right after a server restart — even when no project
  // is "current" on the backend, we still show SOMETHING reasonable.
  const current = (selectedId ? list.find(p => p.id === selectedId) : null) ?? list[0] ?? null
  // Refetch home-summary scoped to the currently-previewed project. Triggers
  // on selection change so Recent Activity + Attention reflect THAT project,
  // not whichever the backend's in-process current happens to be.
  const currentId = current?.id ?? null
  useEffect(() => {
    if (!currentId) return
    let cancelled = false
    fetch(`/api/home-summary?project_id=${encodeURIComponent(currentId)}`)
      .then(r => r.ok ? r.json() : null)
      .then(s => { if (!cancelled && s) setSummary(s) })
      .catch(() => { /* ignore */ })
    return () => { cancelled = true }
  }, [currentId])
  const q = query.trim().toLowerCase()
  const projectMatches = q ? list.filter(p => p.name.toLowerCase().includes(q)) : list
  const currentCounts = { ...(summary?.counts ?? {}), ...(current?.counts ?? {}) }

  // Scope menu visibility by render-site (`central` vs `side`) so the current
  // project, which appears in BOTH columns, doesn't open two menus at once
  // when only one was clicked. The key is `${slot}:${pid}`.
  const menu = (p: Project, slot: 'central' | 'side') => {
    const key = `${slot}:${p.id}`
    return (
      <>
        <button className="home__proj-menu" title="Project actions"
                onClick={e => { e.stopPropagation(); setMenuFor(menuFor === key ? null : key) }}>⋯</button>
        {menuFor === key && (
          <div className="home__menu" onClick={e => e.stopPropagation()}>
            <button onClick={() => { setMenuFor(null); setModal({ kind: 'rename', pid: p.id, name: p.name }) }}>Rename</button>
            <button onClick={() => { setMenuFor(null); verifyRecovery(p.id) }}
                    title="Compare the FS recovery archive against the live DB. Reports any drift.">
              Verify recovery archive
            </button>
            <button className="home__menu-danger"
                    onClick={() => { setMenuFor(null); setModal({ kind: 'delete', pid: p.id, name: p.name }) }}>Delete project</button>
          </div>
        )}
      </>
    )
  }

  // Tiny chip rendered next to a project name when its recovery-report flags
  // missing host-side dependencies. Click → modal with the full report.
  const recoveryChip = (p: Project) => {
    if (!hasMissingDeps(p.id)) return null
    const rep = recoveryReports[p.id]!
    const total =
      (rep.missing.recipes?.length || 0) +
      (rep.missing.capabilities?.length || 0) +
      (rep.missing.entity_types?.length || 0) +
      (rep.missing.tools?.length || 0)
    return (
      <span className="home__recovery-chip"
            title={`Imported with ${total} missing host-side dependencies — click for details`}
            onClick={e => { e.stopPropagation(); setVerifyResult({ pid: p.id, rep: { compat: rep } }) }}>
        ⚠ {total} missing
      </span>
    )
  }

  // First-render guard: projects===null means /api/projects hasn't resolved
  // yet. Without this, we'd flash the zero-project "three cards" empty state
  // for one frame before the real list arrives. Skeleton-empty until loaded.
  if (projects === null) {
    return <div className="home home--loading" />
  }

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
          <div className="home__selector-head">
            <div>
              <span className="home__kicker">Vienna Biocenter</span>
              <h1>Projects</h1>
            </div>
            <div className="home__hub-actions">
              <button className="home__btn" disabled={busy} onClick={trySample}>✦ Try a sample</button>
              <button className="home__btn home__btn--primary" disabled={busy} onClick={startCreate}>+ New project</button>
            </div>
          </div>

          <div className={`home__cols ${list.length > 1 ? 'has-side' : ''}`}>
            {/* Main column — current project detail.
                When no project is open (backend parked on scratch), show a
                "no current project" panel instead of pretending the
                scratch's "Workspace" placeholder is a real project. */}
            {current ? (
              <div className="home__main home__panel home__panel--current">
                <div className="home__cur-head">
                  <div className="home__cur-titles">
                    <span className="home__kicker">Project</span>
                    <h1>{current.name}</h1>
                    {current.last_touched && (
                      <span className="home__muted">Last touched {rel(current.last_touched)}</span>
                    )}
                  </div>
                  <div className="home__cur-actions">
                    {menu(current, 'central')}
                    <button className="home__btn home__btn--primary" disabled={busy}
                            onClick={() => onEnter(current.id)}>Open project →</button>
                  </div>
                </div>

                <div className="home__stats">
                  {home_tiles().map(tile => (
                    <div key={tile.key} className="home__stat">
                      <span className="home__stat-n">{tile.count(currentCounts)}</span>
                      <span className="home__stat-t">{tile.label}</span>
                    </div>
                  ))}
                </div>

                {/* Recent-activity + Attention come from /api/home-summary
                    scoped to the previewed project (refetched in the effect
                    above whenever the selection changes), so the panel is
                    accurate for whatever project the user is browsing. */}
                <div className="home__current-body">
                  <div className="home__section">
                    <div className="home__panel-head">Recent activity</div>
                    {(summary?.recent_events.length ?? 0) === 0 ? (
                      <div className="home__muted">No activity yet.</div>
                    ) : (
                      <div className="home__events">
                        {summary?.recent_events.map(ev => (
                          <button key={ev.id} className="home__event" onClick={() => onEnter(current.id)}>
                            <span className="home__event-kind">{EVENT_LABEL[ev.kind] ?? ev.kind}</span>
                            <span className="home__event-title">{ev.title ?? ''}</span>
                            <span className="home__event-date">{rel(ev.ts)}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="home__section home__section--attention">
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
                </div>
              </div>
            ) : (
              <div className="home__main home__panel home__panel--current home__panel--empty">
                <div className="home__cur-head">
                  <div className="home__cur-titles">
                    <span className="home__kicker">No project open</span>
                    <h1>Pick a project to get started</h1>
                    <span className="home__muted">
                      {list.length > 1
                        ? 'Select one from the list, or create a new project.'
                        : 'Open your project from the list above, or create a new one.'}
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Side column — project list */}
            {list.length > 1 && (
              <aside className="home__side home__panel">
                <div className="home__side-head">
                  <div>
                    <span className="home__kicker">Project list</span>
                    <h2>All projects</h2>
                  </div>
                  <input className="home__side-search" placeholder="Filter projects…"
                         value={query} onChange={e => setQuery(e.target.value)} />
                </div>
                {projectMatches.length === 0 ? (
                  <div className="home__muted">No matches.</div>
                ) : (
                  <div className="home__side-list">
                    {projectMatches.map(p => {
                      const stats = card_order().filter(t => p.counts[t])
                      return (
                        <div
                          key={p.id}
                          className={`home__side-item ${current && current.id === p.id ? 'is-current' : ''}`}
                          role="button"
                          onClick={() => selectProject(p.id)}
                        >
                          <div className="home__side-item-head">
                            <span className="home__side-item-name">{p.name}</span>
                            {recoveryChip(p)}
                            {menu(p, 'side')}
                          </div>
                          <div className="home__side-item-meta">
                            {stats.length ? stats.map(t => `${p.counts[t]} ${t}${p.counts[t] > 1 ? 's' : ''}`).join(' · ') : 'empty'}
                          </div>
                          <div className="home__side-item-foot">Last touched {rel(p.last_touched)}</div>
                        </div>
                      )
                    })}
                  </div>
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

      {verifyResult && (
        <div className="modal-backdrop" onClick={() => setVerifyResult(null)}>
          <div className="modal modal--wide" onClick={e => e.stopPropagation()}>
            <h2 className="modal__title">Recovery archive — {verifyResult.pid}</h2>
            <RecoveryReportBody rep={verifyResult.rep} />
            <div className="modal__actions">
              <button className="home__btn" onClick={() => setVerifyResult(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function RecoveryReportBody({ rep }: { rep: any }) {
  // Two shapes: full drift report (from /verify-recovery) OR compat report
  // (from /recovery-report, wrapped as {compat: ...} by recoveryChip).
  const compat = rep?.compat
  const drift = !rep?.compat ? rep : null
  return (
    <div className="modal__body">
      {drift && (
        <div className="home__recovery-section">
          <div><b>Skew score:</b> {(drift.skew_score * 100).toFixed(1)}%</div>
          <div><b>Counts (live / FS):</b>{' '}
            {Object.entries(drift.counts?.live || {}).map(([t, n]: any) => (
              <span key={t}>{t}: {n} / {drift.counts?.fs?.[t] ?? '?'}{' '}</span>
            ))}
          </div>
          {drift.sample_size > 0 && (
            <div><b>Sample:</b> {drift.sample_mismatches}/{drift.sample_size} mismatched</div>
          )}
          {drift.field_mismatches?.length ? (
            <details>
              <summary>{drift.field_mismatches.length} field mismatch{drift.field_mismatches.length > 1 ? 'es' : ''}</summary>
              <ul>{drift.field_mismatches.slice(0, 20).map((m: any, i: number) => (
                <li key={i}><code>{m.table}/{m.id}.{m.field}</code>: live=<code>{String(m.live)}</code> fs=<code>{String(m.fs)}</code></li>
              ))}</ul>
            </details>
          ) : null}
        </div>
      )}
      {compat && (
        <div className="home__recovery-section">
          <div className="home__muted">
            Source: {compat.source?.aba_commit ?? 'unknown'} ({compat.source?.aba_version ?? 'unknown'}) →
            Host: {compat.host?.aba_commit ?? 'unknown'} ({compat.host?.aba_version ?? 'unknown'})
          </div>
          {(['recipes','capabilities','entity_types','tools'] as const).map(cat => {
            const list = compat.missing?.[cat] ?? []
            if (list.length === 0) return null
            return (
              <div key={cat} className="home__recovery-row">
                <b>Missing {cat.replace('_', ' ')} ({list.length}):</b>{' '}
                <code>{list.join(', ')}</code>
              </div>
            )
          })}
          {compat.artifacts?.missing > 0 && (
            <div className="home__recovery-row">
              <b>Artifacts missing:</b> {compat.artifacts.missing} of {compat.artifacts.present + compat.artifacts.missing}
            </div>
          )}
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
