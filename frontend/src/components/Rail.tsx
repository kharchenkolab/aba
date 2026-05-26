import { useEffect, useState } from 'react'
import Settings from './Settings'
import Skills from './Skills'
import { RailIcon } from './icons'
import './Rail.css'

type ProjectSection = 'threads' | 'claims' | 'data' | 'runs' | 'files'

interface Props {
  onEntitiesChanged: () => void
  view: 'home' | 'workspace'
  onNavigate: (v: 'home' | 'workspace') => void
  collapsed?: boolean
  projectTitle?: string
  activeSection?: ProjectSection
  onProjectSection?: (section: ProjectSection) => void
  sectionCounts?: {
    threads: number
    claims: number
    data: number
    runs: number
    files: number
  }
}

export default function Rail({ view, onNavigate, collapsed = false, projectTitle, sectionCounts, activeSection = 'threads', onProjectSection }: Props) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [skillsOpen, setSkillsOpen] = useState(false)
  const [pendingCount, setPendingCount] = useState(0)

  useEffect(() => {
    let cancelled = false
    async function tick() {
      try {
        const r = await fetch('/api/context-suggestions?status=pending')
        if (r.ok) {
          const ns = await r.json()
          if (!cancelled) setPendingCount(ns.length)
        }
      } catch { /* ignore */ }
    }
    tick()
    const interval = setInterval(tick, settingsOpen ? 1500 : 6000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [settingsOpen])

  const projectSections: { key: ProjectSection; label: string; icon: ProjectSection; count: number }[] = [
    { key: 'threads', label: 'Threads', icon: 'threads', count: sectionCounts?.threads ?? 0 },
    { key: 'claims', label: 'Claims', icon: 'claims' as const, count: sectionCounts?.claims ?? 0 },
    { key: 'data', label: 'Data', icon: 'data' as const, count: sectionCounts?.data ?? 0 },
    { key: 'runs', label: 'Runs', icon: 'runs' as const, count: sectionCounts?.runs ?? 0 },
    { key: 'files', label: 'Files', icon: 'files' as const, count: sectionCounts?.files ?? 0 },
  ]

  return (
    <aside className={`rail ${view === 'workspace' ? 'rail--project' : ''} ${collapsed ? 'rail--collapsed' : ''}`}>
      {view === 'home' && (
        <div className="rail__brand">
          <div className="rail__brand-icon"><RailIcon name="brand" size={34} /></div>
          <span>Vienna<br/>Biocenter</span>
        </div>
      )}

      {view === 'workspace' && collapsed && (
        <button
          className="rail__collapsed-brand"
          title={`Project selection${projectTitle ? ` — ${projectTitle}` : ''}`}
          onClick={() => onNavigate('home')}
        >
          <RailIcon name="brand" size={28} />
        </button>
      )}

      {view === 'workspace' ? (
        <nav className="rail__nav rail__nav--project" aria-label="Project navigation">
          {projectSections.map(section => (
            <button
              key={section.key}
              className={`rail__nav-item rail__nav-item--btn rail__project-tab ${activeSection === section.key ? 'rail__nav-item--active' : ''}`}
              title={collapsed ? `Open ${section.label}` : section.label}
              onClick={() => {
                onNavigate('workspace')
                onProjectSection?.(section.key)
              }}
            >
              <RailIcon name={section.icon} />
              <span>{section.label}</span>
              <small>{section.count}</small>
            </button>
          ))}
        </nav>
      ) : (
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
            className="rail__nav-item rail__nav-item--btn"
            title="Project"
            onClick={() => onNavigate('workspace')}
          >
            <RailIcon name="projects" />
            <span>Project</span>
          </button>
          <button
            className="rail__nav-item rail__nav-item--btn"
            title="Skills — tools and pipelines Guide can drive"
            onClick={() => setSkillsOpen(true)}
          >
            <RailIcon name="skills" />
            <span>Skills</span>
          </button>
          <a className="rail__nav-item" title="Alerts">
            <RailIcon name="alerts" />
            <span>Alerts</span>
          </a>
        </nav>
      )}

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
    </aside>
  )
}
