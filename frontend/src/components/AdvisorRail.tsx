import { useEffect, useState } from 'react'
import { AGENTS, AgentAvatar } from './icons'
import './AdvisorRail.css'

const ADVISORS = AGENTS.map((a, i) => ({ ...a, active: i === 0 }))

interface AdvisorNote {
  id: number
  entity_id: string
  advisor: string
  text: string
  created_at: string
  entity_type?: string | null
  entity_title?: string | null
}

interface Props {
  focusedId: string
  focusedType?: string
  onTry?: (text: string) => void
  onFocus?: (id: string) => void
}

// "3m ago" / "2h ago" / "4d ago" — so an idea that sat around is dated.
function relativeTime(iso: string): string {
  const then = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').getTime()
  if (Number.isNaN(then)) return ''
  const s = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (s < 60) return 'just now'
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}

export default function AdvisorRail({ focusedId, focusedType, onTry, onFocus }: Props) {
  const [notes, setNotes] = useState<AdvisorNote[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)

  // Mark a note tried/dismissed so the lightbulb clears and it stops
  // resurfacing on the next poll.
  function resolveNote(id: number, status: 'tried' | 'dismissed') {
    setNotes(prev => prev.filter(n => n.id !== id))
    fetch(`/api/advisor-notes/${id}/status`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    }).catch(() => {})
  }

  useEffect(() => {
    let cancelled = false
    let stop = false

    async function load() {
      try {
        const r = await fetch(`/api/entities/${encodeURIComponent(focusedId)}/advisor-notes`)
        if (!r.ok) return
        const ns: AdvisorNote[] = await r.json()
        if (!cancelled) setNotes(ns)
      } catch { /* ignore */ }
    }

    setNotes([])
    load()

    // On focusing a dataset (or narrative), request its on-focus advisor
    // after a short idle delay — once per focus. The backend no-ops if the
    // advisor already spoke about this entity.
    let adviseTimer: ReturnType<typeof setTimeout> | undefined
    if (focusedType === 'dataset' || focusedType === 'narrative') {
      adviseTimer = setTimeout(() => {
        fetch(`/api/entities/${encodeURIComponent(focusedId)}/advise`, { method: 'POST' })
          .then(() => { if (!stop) load() })
          .catch(() => {})
      }, 4000)
    }

    // Notes can land asynchronously (advisors complete a few seconds later).
    const tick = setInterval(() => { if (!stop) load() }, 2500)
    return () => {
      cancelled = true; stop = true
      clearInterval(tick)
      if (adviseTimer) clearTimeout(adviseTimer)
    }
  }, [focusedId, focusedType])

  const notesByAdvisor: Record<string, AdvisorNote[]> = {}
  for (const n of notes) (notesByAdvisor[n.advisor] ??= []).push(n)

  return (
    <aside className="adv-rail">
      <div className="adv-header">
        <span>Advisor Team</span>
      </div>

      {ADVISORS.map(adv => {
        const advNotes = notesByAdvisor[adv.key] ?? []
        const hasNotes = advNotes.length > 0
        const isOpen = expanded === adv.key
        return (
          <div
            key={adv.key}
            className={`adv-row ${adv.active ? 'adv-row--active' : ''} ${hasNotes ? 'adv-row--has-notes' : ''}`}
          >
            <button
              className="adv-rowhead"
              onClick={() => hasNotes && setExpanded(o => (o === adv.key ? null : adv.key))}
              style={{ cursor: hasNotes ? 'pointer' : 'default' }}
            >
              <div className="adv-avatar-wrap">
                <AgentAvatar agent={adv.key} size={28} />
                {hasNotes && <span className="adv-bulb" title="Has an idea">💡</span>}
              </div>
              <div className="adv-info">
                <div className="adv-name">{adv.name}</div>
                <div className={`adv-status ${adv.active ? 'adv-status--online' : ''}`}>
                  {adv.active && <span className="dot-green" />}
                  {hasNotes
                    ? `${advNotes.length} idea${advNotes.length === 1 ? '' : 's'} — ${isOpen ? 'hide' : 'view'}`
                    : adv.status}
                </div>
              </div>
            </button>
            {hasNotes && isOpen && (
              <div className="adv-notes">
                {advNotes.map(n => (
                  <div key={n.id} className="adv-note">
                    <div className="adv-note-about">
                      {n.entity_title && (
                        <button
                          className="adv-note-subject"
                          title="Show what this is about"
                          onClick={() => onFocus?.(n.entity_id)}
                        >
                          {n.entity_type ? `${n.entity_type} · ` : ''}{n.entity_title}
                        </button>
                      )}
                      <span className="adv-note-time">{relativeTime(n.created_at)}</span>
                    </div>
                    <p className="adv-note-text">{n.text}</p>
                    <div className="adv-note-actions">
                      {onTry && (
                        <button className="adv-try"
                                onClick={() => { onTry(n.text); resolveNote(n.id, 'tried') }}>
                          Try it →
                        </button>
                      )}
                      <button className="adv-dismiss" title="Dismiss this idea"
                              onClick={() => resolveNote(n.id, 'dismissed')}>
                        Dismiss
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}

    </aside>
  )
}
