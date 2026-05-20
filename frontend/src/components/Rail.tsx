import { useEffect, useState } from 'react'
import Settings from './Settings'
import Skills from './Skills'
import Queues from './Queues'
import './Rail.css'

interface Props {
  onEntitiesChanged: () => void
  view: 'home' | 'workspace'
  onNavigate: (v: 'home' | 'workspace') => void
}

export default function Rail({ onEntitiesChanged, view, onNavigate }: Props) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [skillsOpen, setSkillsOpen] = useState(false)
  const [queuesOpen, setQueuesOpen] = useState(false)
  const [pendingCount, setPendingCount] = useState(0)
  const [activeJobs, setActiveJobs] = useState(0)

  useEffect(() => {
    let cancelled = false
    async function tick() {
      try {
        const r = await fetch('/api/context-suggestions?status=pending')
        if (r.ok) {
          const ns = await r.json()
          if (!cancelled) setPendingCount(ns.length)
        }
        const jr = await fetch('/api/jobs?limit=50')
        if (jr.ok) {
          const js = await jr.json()
          if (!cancelled) {
            setActiveJobs(js.filter((j: { status: string }) =>
              j.status === 'running' || j.status === 'queued').length)
          }
        }
      } catch { /* ignore */ }
    }
    tick()
    const interval = setInterval(tick, (settingsOpen || queuesOpen) ? 1500 : 6000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [settingsOpen, queuesOpen])

  return (
    <aside className="rail">
      <div className="rail__brand">
        <div className="rail__brand-icon">VB</div>
        <span>Vienna<br/>Biocenter</span>
      </div>

      <nav className="rail__nav">
        <button
          className={`rail__nav-item rail__nav-item--btn ${view === 'home' ? 'rail__nav-item--active' : ''}`}
          title="Home"
          onClick={() => onNavigate('home')}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/>
          </svg>
          <span>Home</span>
        </button>
        <button
          className={`rail__nav-item rail__nav-item--btn ${view === 'workspace' ? 'rail__nav-item--active' : ''}`}
          title="Workspace"
          onClick={() => onNavigate('workspace')}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M3 3h8v8H3zm0 10h8v8H3zm10-10h8v8h-8zm0 10h8v8h-8z"/>
          </svg>
          <span>Workspace</span>
        </button>
        <button
          className="rail__nav-item rail__nav-item--btn"
          title="Skills — tools and pipelines Guide can drive"
          onClick={() => setSkillsOpen(true)}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 2L4 6v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V6l-8-4zm-1 14l-4-4 1.4-1.4L11 13.2l4.6-4.6L17 10l-6 6z"/>
          </svg>
          <span>Skills</span>
        </button>
        <button
          className="rail__nav-item rail__nav-item--btn"
          title="Queues — background jobs"
          onClick={() => setQueuesOpen(true)}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M4 6h16v2H4zm0 5h16v2H4zm0 5h16v2H4z"/>
          </svg>
          <span>Queues</span>
          {activeJobs > 0 && <span className="rail__nav-badge">{activeJobs}</span>}
        </button>
        <a className="rail__nav-item" title="Alerts">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 22c1.1 0 2-.9 2-2h-4a2 2 0 002 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/>
          </svg>
          <span>Alerts</span>
        </a>
      </nav>

      <button
        className="rail__user"
        onClick={() => setSettingsOpen(true)}
        title="Account & settings"
      >
        <div className="rail__avatar">PP</div>
        <span>Peter</span>
        {pendingCount > 0 && <span className="rail__badge">{pendingCount}</span>}
      </button>
      {settingsOpen && <Settings onClose={() => setSettingsOpen(false)} />}
      {skillsOpen && <Skills onClose={() => setSkillsOpen(false)} />}
      {queuesOpen && (
        <Queues onClose={() => setQueuesOpen(false)} onJobsChanged={onEntitiesChanged} />
      )}
    </aside>
  )
}
