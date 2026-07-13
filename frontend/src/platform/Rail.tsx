import { useEffect, useRef, useState } from 'react'
import Settings from '../components/Settings'
import FirstRunGate from '../components/FirstRunGate'
import ModuleToasts from '../components/ModuleToasts'
// Rail icons dispatch through the lib registry — bio registers its
// glyph set at startup; the shell reads via lib/ to satisfy the
// platform-purity lint (no `../bio/*` imports).
import { rail_icon_for } from '../lib/railIcons'
import './Rail.css'

type ProjectSection = 'threads' | 'claims' | 'data' | 'runs' | 'results' | 'files'

/** Thin platform-side wrapper around the bio rail-icon registry. The
 *  shell uses this in place of the old `<RailIcon name="..." />` so any
 *  bio domain can replace the entire glyph set by registering its own. */
function RailIcon({ name, size = 24 }: { name: string; size?: number }) {
  const Comp = rail_icon_for(name)
  if (!Comp) {
    // Unknown name — render a small ring so a layout slot is still
    // visible (and a missing icon shows up loudly during dev).
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor">
        <circle cx="12" cy="12" r="8" strokeWidth="1.6" />
      </svg>
    )
  }
  return <Comp size={size} />
}

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
    results: number
    files: number
  }
}

export default function Rail({ view, onNavigate, collapsed = false, projectTitle, sectionCounts, activeSection = 'threads', onProjectSection }: Props) {
  const [settingsOpen, setSettingsOpen] = useState(false)
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

  type SectionDef = { key: ProjectSection; label: string; icon: ProjectSection; count: number; visible: boolean }
  const c = (k: keyof NonNullable<typeof sectionCounts>) => sectionCounts?.[k] ?? 0
  // A tab is "earned" once it has content — or while it's the active tab, so the
  // user is never stranded on one that just emptied out.
  const earned = (k: ProjectSection) => c(k) > 0 || activeSection === k
  // Files is the on-disk browser spanning run outputs + datasets + results, so it
  // counts as populated the moment any Run or Result exists — including a run that
  // wrote only an .h5ad (no figure/table entity, so files-count stays 0).
  const hasItems = (k: ProjectSection): boolean =>
    k === 'files' ? (c('files') > 0 || c('runs') > 0 || c('results') > 0) : c(k) > 0
  // Sections that are ALWAYS shown regardless of content, because they're
  // the entry point for the user to START creating that kind of thing.
  // Data: the "+ create dataset / upload" affordance lives here, so it
  // must be reachable even on a brand-new empty project (PK policy
  // 2026-06-05). Threads is implicitly always populated (a Main thread
  // exists), so it doesn't need to be listed here.
  const ALWAYS_VISIBLE: ProjectSection[] = ['data']
  // Second filter, on top of the earned-slot reveal: pills only appear when
  // there is a real choice to make — ≥2 populated sections, OR one of the
  // ALWAYS_VISIBLE tabs is in play (so a brand-new project still shows the
  // Data tab + Threads as the nav). A truly empty project with no
  // always-visible exemption still hides the nav (back to the chat).
  const ALL_SECTIONS: ProjectSection[] = ['threads', 'data', 'claims', 'results', 'runs', 'files']
  const meaningfulCount = ALL_SECTIONS.filter(hasItems).length
  const showPills = meaningfulCount > 1 || !hasItems(activeSection) || ALWAYS_VISIBLE.length > 0
  // A pill earns its FIXED slot only if its section has content (or it's the
  // current tab). ALWAYS_VISIBLE tabs render regardless. Hidden-but-present
  // sections still reserve their slot so the visible subset never shifts.
  const slotVisible = (k: ProjectSection, earnedSlot: boolean): boolean =>
    showPills && earnedSlot && (ALWAYS_VISIBLE.includes(k) || hasItems(k) || activeSection === k)
  // Top group: Threads · Data · Claims (Claims last so it reveals without nudging
  // the others). Bottom group: Results · Runs · Files, each purely agent-generated.
  const navSections: SectionDef[] = [
    { key: 'threads', label: 'Threads', icon: 'threads', count: c('threads'), visible: slotVisible('threads', true) },
    { key: 'data', label: 'Data', icon: 'data' as const, count: c('data'), visible: slotVisible('data', true) },
    { key: 'claims', label: 'Claims', icon: 'claims' as const, count: c('claims'), visible: slotVisible('claims', earned('claims')) },
  ]
  const artifactSections: SectionDef[] = [
    { key: 'results', label: 'Results', icon: 'results' as const, count: c('results'), visible: slotVisible('results', earned('results')) },
    { key: 'runs', label: 'Runs', icon: 'runs' as const, count: c('runs'), visible: slotVisible('runs', earned('runs')) },
    { key: 'files', label: 'Files', icon: 'files' as const, count: c('files'),
      visible: slotVisible('files', earned('files') || c('runs') > 0 || c('results') > 0) },
  ]

  // Animate only tabs that transition hidden→visible (slide in from the left, as
  // if emerging from under the rail). Tabs present on first render don't animate.
  const visibleKeys = [...navSections, ...artifactSections].filter(s => s.visible).map(s => s.key)
  const prevVisible = useRef<Set<ProjectSection> | null>(null)
  const justAppeared = (k: ProjectSection) =>
    prevVisible.current !== null && !prevVisible.current.has(k)
  useEffect(() => { prevVisible.current = new Set(visibleKeys) })

  // Every tab keeps a FIXED slot, shown or not, so positions never shift as the
  // subset changes. A hidden tab leaves its slot reserved but invisible; an
  // earned tab slides into its own slot from the left (rail__nav-item--enter).
  const renderTab = (section: SectionDef) => (
    <button
      key={section.key}
      className={`rail__nav-item rail__nav-item--btn rail__project-tab rail__project-tab--${section.key}`
        + ` ${activeSection === section.key ? 'rail__nav-item--active' : ''}`
        + ` ${section.visible ? (justAppeared(section.key) ? 'rail__nav-item--enter' : '') : 'rail__project-tab--reserved'}`}
      title={collapsed ? `Open ${section.label}` : section.label}
      onClick={() => { onNavigate('workspace'); onProjectSection?.(section.key) }}
      tabIndex={section.visible ? undefined : -1}
      aria-hidden={section.visible ? undefined : true}
    >
      <RailIcon name={section.icon} />
      <span>{section.label}</span>
      {section.count > 0 && <small>{section.count}</small>}
    </button>
  )
  const anyArtifactVisible = artifactSections.some(s => s.visible)

  return (
    <aside className={`rail ${view === 'workspace' ? 'rail--project' : 'rail--home'} ${collapsed ? 'rail--collapsed' : ''}`}>
      {view === 'home' && (
        <button
          className="rail__home-brand"
          title="Vienna Biocenter project selection"
          onClick={() => onNavigate('home')}
        >
          <RailIcon name="brand" size={28} />
        </button>
      )}

      {view === 'workspace' && (
        <button
          className="rail__collapsed-brand"
          title={`Project selection${projectTitle ? ` — ${projectTitle}` : ''}`}
          onClick={() => onNavigate('home')}
        >
          <RailIcon name="brand" size={28} />
        </button>
      )}

      {view === 'workspace' && showPills ? (
        <nav className="rail__nav rail__nav--project" aria-label="Project navigation">
          {navSections.map(renderTab)}
          {/* Always present so the lower slots stay fixed; only the hairline
              shows once the bottom group has an earned tab. */}
          <div className={`rail__nav-group-sep ${anyArtifactVisible ? '' : 'rail__nav-group-sep--hidden'}`} aria-hidden="true" />
          {artifactSections.map(renderTab)}
        </nav>
      ) : (
        // No pills (brand-new project / single populated tab) or home view: an
        // empty flex spacer keeps the user button pinned to the bottom.
        <div className="rail__home-spacer" aria-hidden="true" />
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
      <FirstRunGate settingsOpen={settingsOpen} onOpenSettings={() => setSettingsOpen(true)} />
      <ModuleToasts />
    </aside>
  )
}
