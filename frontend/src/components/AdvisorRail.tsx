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
  { name: 'Methodologist', key: 'methodologist', color: 'var(--metho)',    status: 'on run' },
  { name: 'Skeptic',       key: 'skeptic',       color: 'var(--skeptic)',  status: 'on promote' },
  { name: 'Explorer',      key: 'explorer',      color: 'var(--explorer)', status: 'on data' },
  { name: 'Stylist',       key: 'stylist',       color: 'var(--stylist)',  status: 'on write' },
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
  focusedType?: string
  onTry?: (text: string) => void
}

export default function AdvisorRail({ focusedId, focusedType, onTry }: Props) {
  const [notes, setNotes] = useState<AdvisorNote[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)

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
                <Avatar color={adv.color} name={adv.name} />
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
                    <p className="adv-note-text">{n.text}</p>
                    {onTry && (
                      <button className="adv-try" onClick={() => onTry(n.text)}>Try it →</button>
                    )}
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
