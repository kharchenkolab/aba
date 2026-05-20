import { useEffect, useState } from 'react'
import Settings from './Settings'
import Skills from './Skills'
import Queues from './Queues'
import { RailIcon } from './icons'
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
        <div className="rail__brand-icon"><RailIcon name="brand" size={34} /></div>
        <span>Vienna<br/>Biocenter</span>
      </div>

      <nav className="rail__nav">
        <button
          className={`rail__nav-item rail__nav-item--btn ${view === 'home' ? 'rail__nav-item--active' : ''}`}
          title="Home"
          onClick={() => onNavigate('home')}
        >
          <RailIcon name="home" />
          <span>Home</span>
        </button>
        <button
          className={`rail__nav-item rail__nav-item--btn ${view === 'workspace' ? 'rail__nav-item--active' : ''}`}
          title="Workspace"
          onClick={() => onNavigate('workspace')}
        >
          <RailIcon name="projects" />
          <span>Workspace</span>
        </button>
        <button
          className="rail__nav-item rail__nav-item--btn"
          title="Skills — tools and pipelines Guide can drive"
          onClick={() => setSkillsOpen(true)}
        >
          <RailIcon name="skills" />
          <span>Skills</span>
        </button>
        <button
          className="rail__nav-item rail__nav-item--btn"
          title="Queues — background jobs"
          onClick={() => setQueuesOpen(true)}
        >
          <RailIcon name="queues" />
          <span>Queues</span>
          {activeJobs > 0 && <span className="rail__nav-badge">{activeJobs}</span>}
        </button>
        <a className="rail__nav-item" title="Alerts">
          <RailIcon name="alerts" />
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
