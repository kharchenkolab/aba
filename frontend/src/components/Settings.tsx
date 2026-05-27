/**
 * Settings — pending context-policy suggestions surfaced by the adaptive
 * loop (§3.6). Reviewer can Approve (appends to the per-type policy file
 * that the context service concatenates) or Reject.
 */
import { useCallback, useEffect, useState } from 'react'
import './Settings.css'

interface Suggestion {
  id: number
  session_id: string | null
  entity_type: string | null
  trigger: string
  suggestion: string
  status: string
  created_at: string
}

interface EventRow {
  id: number
  kind: string
  entity_id: string | null
  title: string | null
  detail: Record<string, unknown> | null
  ts: string
}

interface Props {
  onClose: () => void
}

const EVENT_LABEL: Record<string, string> = {
  entity_created: 'created',
  scenario_created: 'scenario',
  advisor_note: 'advisor note',
  suggestion_logged: 'context suggestion',
}

export default function Settings({ onClose }: Props) {
  const [tab, setTab] = useState<'suggestions' | 'activity'>('suggestions')
  const [pending, setPending] = useState<Suggestion[]>([])
  const [events, setEvents] = useState<EventRow[]>([])

  const refresh = useCallback(async () => {
    try {
      const r = await fetch('/api/context-suggestions?status=pending')
      if (r.ok) setPending(await r.json())
      const e = await fetch('/api/events?limit=100')
      if (e.ok) setEvents(await e.json())
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  async function act(id: number, action: 'approve' | 'reject') {
    await fetch(`/api/context-suggestions/${id}/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    })
    refresh()
  }

  async function rejectAll() {
    if (pending.length === 0) return
    if (!window.confirm(`Reject all ${pending.length} pending suggestion${pending.length === 1 ? '' : 's'}?`)) return
    await fetch('/api/context-suggestions/reject-all', { method: 'POST' })
    refresh()
  }

  return (
    <div className="settings-backdrop" onClick={onClose}>
      <div className="settings" onClick={e => e.stopPropagation()}>
        <div className="settings__head">
          <h2>Settings</h2>
          <button onClick={onClose} className="settings__close" title="Close">×</button>
        </div>
        <div className="settings__tabs">
          <button
            className={`settings__tab ${tab === 'suggestions' ? 'is-active' : ''}`}
            onClick={() => setTab('suggestions')}
          >
            Context suggestions{pending.length > 0 ? ` (${pending.length})` : ''}
          </button>
          <button
            className={`settings__tab ${tab === 'activity' ? 'is-active' : ''}`}
            onClick={() => setTab('activity')}
          >
            Activity
          </button>
        </div>

        {tab === 'suggestions' && (
          <>
            <p className="settings__hint">
              After complex sessions, Guide reflects on what context would have helped.
              Approve to append to the per-entity-type policy injected into future
              system prompts. Reject to discard. Suggestions older than 14 days
              auto-stale; review them sooner if you want to keep them.
            </p>
            {pending.length === 0 ? (
              <div className="settings__empty">No pending suggestions.</div>
            ) : (
              <div className="settings__list">
                {pending.length > 1 && (
                  <div className="settings__bulkbar">
                    <button onClick={rejectAll} className="settings__bulk-reject">
                      Reject all ({pending.length})
                    </button>
                  </div>
                )}
                {pending.map(s => (
                  <div key={s.id} className="suggestion">
                    <div className="suggestion__head">
                      <span className="suggestion__type">{s.entity_type ?? 'workspace'}</span>
                      <span className="suggestion__trigger">{s.trigger}</span>
                      <span className="suggestion__date">{new Date(s.created_at).toLocaleString()}</span>
                    </div>
                    <p className="suggestion__text">{s.suggestion}</p>
                    <div className="suggestion__actions">
                      <button onClick={() => act(s.id, 'reject')} className="suggestion__reject">Reject</button>
                      <button onClick={() => act(s.id, 'approve')} className="suggestion__approve">Approve</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {tab === 'activity' && (
          <>
            <p className="settings__hint">
              An append-only log of everything that's happened in the project.
            </p>
            {events.length === 0 ? (
              <div className="settings__empty">No activity yet.</div>
            ) : (
              <div className="settings__list">
                {events.map(ev => (
                  <div key={ev.id} className="event">
                    <span className="event__kind">{EVENT_LABEL[ev.kind] ?? ev.kind}</span>
                    {ev.detail?.type != null && (
                      <span className="event__etype">{String(ev.detail.type)}</span>
                    )}
                    {ev.detail?.advisor != null && (
                      <span className="event__etype">{String(ev.detail.advisor)}</span>
                    )}
                    <span className="event__title">{ev.title ?? ev.entity_id ?? ''}</span>
                    <span className="event__date">{new Date(ev.ts).toLocaleString()}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
