import { useEffect, useState } from 'react'
import './AdvisorRail.css'

interface Advisor {
  name: string
  key: string
  color: string
  status: string
  active?: boolean
}

const ADVISORS: Advisor[] = [
  { name: 'Guide',         key: 'guide',         color: 'var(--guide)',    status: 'online', active: true },
  { name: 'Methodologist', key: 'methodologist', color: 'var(--metho)',    status: 'quiet' },
  { name: 'Skeptic',       key: 'skeptic',       color: 'var(--skeptic)',  status: 'on demand' },
  { name: 'Explorer',      key: 'explorer',      color: 'var(--explorer)', status: 'quiet' },
  { name: 'Stylist',       key: 'stylist',       color: 'var(--stylist)',  status: 'quiet' },
]

interface AdvisorNote {
  id: number
  entity_id: string
  advisor: string
  text: string
  created_at: string
}

function Avatar({ color, name }: { color: string; name: string }) {
  return (
    <div className="adv-avatar" style={{ background: color }}>
      {name[0]}
    </div>
  )
}

interface Props {
  focusedId: string
}

export default function AdvisorRail({ focusedId }: Props) {
  const [notes, setNotes] = useState<AdvisorNote[]>([])

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
    // Notes can land asynchronously (Skeptic fires on promote, completes
    // a few seconds later). Lightly poll while the entity is focused.
    const tick = setInterval(() => { if (!stop) load() }, 2500)
    return () => { cancelled = true; stop = true; clearInterval(tick) }
  }, [focusedId])

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
        return (
          <div
            key={adv.key}
            className={`adv-row ${adv.active ? 'adv-row--active' : ''} ${hasNotes ? 'adv-row--has-notes' : ''}`}
          >
            <Avatar color={adv.color} name={adv.name} />
            <div className="adv-info">
              <div className="adv-name">{adv.name}</div>
              <div className={`adv-status ${adv.active ? 'adv-status--online' : ''}`}>
                {adv.active && <span className="dot-green" />}
                {hasNotes ? `${advNotes.length} note${advNotes.length === 1 ? '' : 's'}` : adv.status}
              </div>
              {hasNotes && (
                <div className="adv-notes">
                  {advNotes.map(n => (
                    <div key={n.id} className="adv-note">
                      <p className="adv-note-text">{n.text}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )
      })}

      <div className="adv-footer">
        <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor" style={{ color: 'var(--text-4)' }}>
          <path d="M10 2a8 8 0 100 16A8 8 0 0010 2zm1 11H9v-2h2v2zm0-4H9V7h2v2z"/>
        </svg>
        Team settings
      </div>
    </aside>
  )
}
