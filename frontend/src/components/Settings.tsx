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

interface Props {
  onClose: () => void
}

export default function Settings({ onClose }: Props) {
  const [pending, setPending] = useState<Suggestion[]>([])

  const refresh = useCallback(async () => {
    try {
      const r = await fetch('/api/context-suggestions?status=pending')
      if (r.ok) setPending(await r.json())
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

  return (
    <div className="settings-backdrop" onClick={onClose}>
      <div className="settings" onClick={e => e.stopPropagation()}>
        <div className="settings__head">
          <h2>Pending context suggestions</h2>
          <button onClick={onClose} className="settings__close" title="Close">×</button>
        </div>
        <p className="settings__hint">
          After complex sessions, Guide reflects on what context would have helped.
          Approve to append to the per-entity-type policy that's injected into
          future system prompts. Reject to discard.
        </p>
        {pending.length === 0 ? (
          <div className="settings__empty">No pending suggestions.</div>
        ) : (
          <div className="settings__list">
            {pending.map(s => (
              <div key={s.id} className="suggestion">
                <div className="suggestion__head">
                  <span className="suggestion__type">{s.entity_type ?? 'workspace'}</span>
                  <span className="suggestion__trigger">{s.trigger}</span>
                  <span className="suggestion__date">
                    {new Date(s.created_at).toLocaleString()}
                  </span>
                </div>
                <p className="suggestion__text">{s.suggestion}</p>
                <div className="suggestion__actions">
                  <button onClick={() => act(s.id, 'reject')} className="suggestion__reject">
                    Reject
                  </button>
                  <button onClick={() => act(s.id, 'approve')} className="suggestion__approve">
                    Approve
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
