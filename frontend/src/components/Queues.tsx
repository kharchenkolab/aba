/**
 * Queues (Phase 17) — background job monitor. Polls /api/jobs while open;
 * shows status, kind, elapsed, log tail, and a cancel button for
 * running/queued jobs. On completion, calls onJobsChanged so the tree
 * refreshes (background jobs register entities outside any chat SSE).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import './Queues.css'

interface Job {
  id: string
  kind: string
  title: string | null
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  focus_entity_id: string | null
  log_tail: string | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

interface Props {
  onClose: () => void
  onJobsChanged: () => void
}

const STATUS_META: Record<string, { label: string; cls: string }> = {
  queued:    { label: 'Queued',    cls: 'q--queued' },
  running:   { label: 'Running',   cls: 'q--running' },
  done:      { label: 'Done',      cls: 'q--done' },
  failed:    { label: 'Failed',    cls: 'q--failed' },
  cancelled: { label: 'Cancelled', cls: 'q--cancelled' },
}

function elapsed(job: Job): string {
  const start = job.started_at ? new Date(job.started_at).getTime() : null
  if (!start) return '—'
  const end = job.finished_at ? new Date(job.finished_at).getTime() : Date.now()
  const s = Math.max(0, Math.round((end - start) / 1000))
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

export default function Queues({ onClose, onJobsChanged }: Props) {
  const [jobs, setJobs] = useState<Job[]>([])
  const prevActive = useRef<number>(0)

  const refresh = useCallback(async () => {
    try {
      const r = await fetch('/api/jobs')
      if (!r.ok) return
      const js: Job[] = await r.json()
      setJobs(js)
      // When the count of active jobs drops, something finished → refresh tree.
      const active = js.filter(j => j.status === 'running' || j.status === 'queued').length
      if (active < prevActive.current) onJobsChanged()
      prevActive.current = active
    } catch { /* ignore */ }
  }, [onJobsChanged])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 1500)
    return () => clearInterval(t)
  }, [refresh])

  async function cancel(id: string) {
    await fetch(`/api/jobs/${id}/cancel`, { method: 'POST' })
    refresh()
  }

  return (
    <div className="queues-backdrop" onClick={onClose}>
      <div className="queues" onClick={e => e.stopPropagation()}>
        <div className="queues__head">
          <h2>Queues</h2>
          <span className="queues__count">{jobs.length}</span>
          <button onClick={onClose} className="queues__close" title="Close">×</button>
        </div>
        <p className="queues__hint">
          Background analyses run here without blocking the conversation.
          Figures register automatically when a job finishes.
        </p>
        {jobs.length === 0 ? (
          <div className="queues__empty">No jobs yet. Long analyses the Guide runs in the background will appear here.</div>
        ) : (
          <div className="queues__list">
            {jobs.map(j => {
              const meta = STATUS_META[j.status] ?? STATUS_META.queued
              const active = j.status === 'queued' || j.status === 'running'
              return (
                <div key={j.id} className={`job ${meta.cls}`}>
                  <div className="job__row">
                    <span className={`job__status ${meta.cls}`}>{meta.label}</span>
                    <span className="job__title">{j.title || j.kind}</span>
                    <span className="job__elapsed">{elapsed(j)}</span>
                    {active && (
                      <button className="job__cancel" onClick={() => cancel(j.id)}>Cancel</button>
                    )}
                  </div>
                  {(j.log_tail || j.error) && (
                    <pre className="job__log">{j.error || j.log_tail}</pre>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
