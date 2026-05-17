import React from 'react'
import './AdvisorRail.css'

interface Advisor {
  name: string
  color: string
  status: string
  active?: boolean
}

const ADVISORS: Advisor[] = [
  { name: 'Guide',        color: 'var(--guide)',   status: 'online', active: true },
  { name: 'Methodologist', color: 'var(--metho)',   status: 'quiet' },
  { name: 'Skeptic',      color: 'var(--skeptic)', status: 'quiet' },
  { name: 'Explorer',     color: 'var(--explorer)',status: 'quiet' },
  { name: 'Stylist',      color: 'var(--stylist)', status: 'quiet' },
]

function Avatar({ color, name }: { color: string; name: string }) {
  return (
    <div className="adv-avatar" style={{ background: color }}>
      {name[0]}
    </div>
  )
}

export default function AdvisorRail() {
  return (
    <aside className="adv-rail">
      <div className="adv-header">
        <span>Advisor Team</span>
      </div>

      {ADVISORS.map(adv => (
        <div className={`adv-row ${adv.active ? 'adv-row--active' : ''}`} key={adv.name}>
          <Avatar color={adv.color} name={adv.name} />
          <div className="adv-info">
            <div className="adv-name">{adv.name}</div>
            <div className={`adv-status ${adv.active ? 'adv-status--online' : ''}`}>
              {adv.active && <span className="dot-green" />}
              {adv.status}
            </div>
          </div>
        </div>
      ))}

      <div className="adv-footer">
        <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor" style={{ color: 'var(--text-4)' }}>
          <path d="M10 2a8 8 0 100 16A8 8 0 0010 2zm1 11H9v-2h2v2zm0-4H9V7h2v2z"/>
        </svg>
        Team settings
      </div>
    </aside>
  )
}
