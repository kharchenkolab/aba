import { useState } from 'react'
import type { ReactNode } from 'react'
import './ProjectTree.css'
import type { Entity, EntityType } from '../types'
import EntityMenu from './EntityMenu'
import { RailIcon, type RailIconName } from './icons'
import FilesView from './FilesView'
import type { FileNode } from '../viewers/types'

type ProjectSection = 'threads' | 'claims' | 'data' | 'runs' | 'results' | 'files'

interface Props {
  entities: Entity[]
  focusedId: string
  activeSection: ProjectSection
  onFocus: (id: string) => void
  onViewFile?: (node: FileNode) => void   // Files tab: open synthesized files in central column
  onChange: () => void
  currentThread: string
  onSelectThread: (id: string) => void
  onOpenOverview: () => void
  onOpenThreadOverview: (id: string) => void
}

const SECTION_CONFIG: Record<Exclude<ProjectSection, 'threads'>, {
  label: string
  types: EntityType[]
  icon: RailIconName
  empty: string
  sectionLabel: string
  filters: string[]
}> = {
  claims: {
    label: 'Claims',
    types: ['claim'],
    icon: 'claims',
    empty: 'No claims yet.',
    sectionLabel: 'Active claims',
    filters: ['Active', 'Contested', 'All'],
  },
  data: {
    label: 'Data',
    types: ['dataset'],
    icon: 'data',
    empty: 'No datasets yet.',
    sectionLabel: 'Active datasets',
    filters: ['Active', 'Imported', 'All'],
  },
  runs: {
    label: 'Runs',
    types: ['analysis'],
    icon: 'runs',
    empty: 'No runs yet.',
    sectionLabel: 'Recent runs',
    filters: ['Active', 'Attention', 'Complete', 'All'],
  },
  results: {
    label: 'Results',
    types: ['figure', 'table', 'result', 'note', 'narrative'],
    icon: 'results',
    empty: 'No results yet.',
    sectionLabel: 'Project results',
    filters: ['All', 'Figures', 'Tables'],
  },
  files: {
    label: 'Files',
    types: [],   // virtual files view — populated by F2; this rail entry is a placeholder until then.
    icon: 'files',
    empty: 'Virtual files view coming soon.',
    sectionLabel: 'Files',
    filters: ['All'],
  },
}

const DEFAULT_FILTERS: Record<ProjectSection, string> = {
  threads: 'Active',
  claims: 'Active',
  data: 'Active',
  runs: 'Active',
  results: 'All',
  files: 'All',
}

const STATUS_ICON: Record<string, string> = {
  running: '▶',
  superseded: '↺',
  failed: '⚠',
  archived: '📦',
}

const SECTION_CAP = 8   // items shown per section before "show all"

function OverviewIcon({ className = 'tree__overview-icon', size = 17 }: { className?: string; size?: number }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round">
      <rect x="4" y="4" width="6" height="6" rx="1.5" />
      <rect x="14" y="4" width="6" height="6" rx="1.5" />
      <rect x="4" y="14" width="6" height="6" rx="1.5" />
      <path d="M14 17h6M17 14v6" />
    </svg>
  )
}

export default function ProjectTree({ entities, focusedId, activeSection, onFocus, onViewFile, onChange, currentThread, onSelectThread, onOpenOverview, onOpenThreadOverview }: Props) {
  const [query, setQuery] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [showAll, setShowAll] = useState<Record<string, boolean>>({})
  const [sectionFilters, setSectionFilters] = useState<Record<ProjectSection, string>>(DEFAULT_FILTERS)

  const workspace = entities.find(e => e.id === 'workspace')

  // Apply search + archived filter client-side. Server-side same path will
  // be used when projects exceed ~hundreds of entities.
  const q = query.trim().toLowerCase()
  const filterFn = (e: Entity) => {
    if (e.id === 'workspace') return false
    // Superseded figures are version history — only the latest shows in the
    // tree; older versions live in the figure's history drawer.
    if (e.status === 'superseded') return false
    if (!showArchived && e.status === 'archived') return false
    if (q && !e.title.toLowerCase().includes(q)) return false
    return true
  }
  const visible = entities.filter(filterFn)
  const byId = new Map(entities.map(e => [e.id, e]))
  // Named threads only; the default thread is represented by the "Main thread"
  // chip (selects the 'default' alias), not a duplicate named row.
  const threads = entities.filter(e => e.type === 'thread' && e.status !== 'archived' && !e.metadata?.is_default)
  const projectTitle = workspace?.title ?? 'Workspace'

  async function newThread() {
    const r = await fetch('/api/threads', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'New investigation' }),
    })
    if (r.ok) { const t = await r.json(); onChange(); onSelectThread(t.id) }
  }

  async function renameProject(name: string) {
    const t = name.trim()
    setEditingTitle(false)
    if (!t || t === projectTitle) return
    await fetch('/api/entities/workspace', {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: t }),
    })
    onChange()
  }

  const activeEntityRows = visible.filter(e => e.status !== 'archived')
  const threadEntityFor = (id: string) => id === 'default'
    ? entities.find(e => e.type === 'thread' && !!e.metadata?.is_default)
    : entities.find(e => e.id === id && e.type === 'thread')
  const evidenceRefs = (e: Entity) => [
    ...((e.metadata?.evidence_ids as string[] | undefined) ?? []),
    ...((e.metadata?.supporting_findings as string[] | undefined) ?? []),
    ...((e.metadata?.evidence as string[] | undefined) ?? []),
    ...((e.metadata?.supporting_results as string[] | undefined) ?? []),
    ...(((e.metadata?.members as { ref?: string }[] | undefined) ?? []).map(m => m.ref).filter((ref): ref is string => !!ref)),
  ]
  const belongsToThread = (e: Entity, canonicalId: string, seen = new Set<string>()): boolean => {
    if (e.metadata?.thread_id === canonicalId) return true
    if (seen.has(e.id)) return false
    seen.add(e.id)
    return evidenceRefs(e).some(id => {
      const ref = byId.get(id)
      return !!ref && belongsToThread(ref, canonicalId, seen)
    })
  }
  const countForThread = (id: string, predicate: (e: Entity) => boolean) => {
    const threadEntity = threadEntityFor(id)
    const canonicalId = threadEntity?.id ?? id
    return activeEntityRows.filter(e => belongsToThread(e, canonicalId) && predicate(e)).length
  }
  const firstText = (...values: unknown[]) => values.find(v => typeof v === 'string' && v.trim()) as string | undefined
  const countLabel = (n: number, singular: string, plural = `${singular}s`) => n > 0 ? `${n} ${n === 1 ? singular : plural}` : ''
  const runOutputMeta = (entity: Entity) => {
    const run = (entity.metadata?.run ?? {}) as { outputs?: { kind?: string }[]; bulk?: { count?: number } }
    const outputs = run.outputs ?? []
    const plots = outputs.filter(o => o.kind === 'figure' || o.kind === 'view').length
    const tables = outputs.filter(o => o.kind === 'table').length
    const files = outputs.filter(o => o.kind === 'file').length + (run.bulk?.count ?? 0)
    return [
      countLabel(plots, 'plot'),
      countLabel(tables, 'table'),
      countLabel(files, 'file'),
      outputs.length === 0 && !run.bulk?.count ? firstText(entity.metadata?.summary, entity.metadata?.text, entity.notes) : '',
    ].filter(Boolean)
  }
  const entityMeta = (entity: Entity) => {
    const meta: ReactNode[] = []
    const statusIcon = STATUS_ICON[entity.status]
    const appendText = (values: (string | undefined)[]) => {
      values.filter((value): value is string => !!value).forEach((value, i) => meta.push(<span key={`text-${i}`}>{value}</span>))
    }
    if (entity.type === 'dataset') {
      const description = firstText(entity.metadata?.description, entity.metadata?.summary, entity.metadata?.text, entity.notes, entity.metadata?.source, entity.metadata?.path)
      if (description) meta.push(<span key="description">{description}</span>)
    } else if (entity.type === 'analysis') {
      appendText(runOutputMeta(entity))
    } else if (entity.type === 'claim' && entity.metadata?.confidence) {
      meta.push(
        <span key="confidence" className={`state-tag state-tag--confidence tree__confidence--${entity.metadata.confidence}`}>
          {String(entity.metadata.confidence)}
        </span>,
      )
    } else if (entity.status && entity.status !== 'active') {
      meta.push(<span key="status" className="state-tag state-tag--muted">{statusIcon ? `${statusIcon} ` : ''}{entity.status}</span>)
    }
    if (entity.pinned) meta.push(<span key="pinned">pinned</span>)
    entity.tags.slice(0, 2).forEach(tag => meta.push(<span key={tag}>{tag}</span>))
    return meta
  }
  const selectFilter = (section: ProjectSection, filter: string) => {
    setSectionFilters(s => ({ ...s, [section]: filter }))
    setShowAll(s => ({ ...s, [`${section}:${filter}`]: false }))
  }
  const threadMatchesFilter = (thread: { id: string; lifecycle: string }, filter: string) => {
    const attentionCount = countForThread(thread.id, e =>
      e.status === 'failed' ||
      e.status === 'running' ||
      (e.type === 'claim' && ['contested', 'refuted'].includes(String(e.metadata?.confidence ?? ''))),
    )
    switch (filter) {
      case 'Attention': return thread.lifecycle !== 'open' || attentionCount > 0
      case 'All': return true
      case 'Active':
      default: return thread.lifecycle === 'open'
    }
  }
  const entityMatchesFilter = (entity: Entity, section: Exclude<ProjectSection, 'threads'>, filter: string) => {
    if (filter === 'All') return true
    if (section === 'claims') {
      if (filter === 'Contested') return ['contested', 'refuted'].includes(String(entity.metadata?.confidence ?? '')) || entity.status === 'failed'
      return entity.status === 'active'
    }
    if (section === 'data') {
      if (filter === 'Imported') return !!entity.artifact_path || !!entity.metadata?.source || !!entity.metadata?.path
      return entity.status === 'active'
    }
    if (section === 'runs') {
      if (filter === 'Attention') return entity.status === 'failed' || entity.status === 'running'
      if (filter === 'Complete') return entity.status === 'active' || entity.status === 'completed'
      return entity.status === 'active' || entity.status === 'running'
    }
    if (section === 'results') {
      if (filter === 'Figures') return entity.type === 'figure'
      if (filter === 'Tables') return entity.type === 'table'
    }
    return true
  }

  return (
    <aside className="tree">
      <div className="tree__project-plate">
        <button className="tree__project-copy" title="Focus project" onClick={() => onFocus('workspace')}>
          <span className="tree__project-kicker">Project</span>
          <span className="tree__project-title">{projectTitle}</span>
        </button>
        <button className="tree__project-overview" title="Project overview — all items, by status"
                onClick={e => { e.stopPropagation(); onOpenOverview() }}>
          <OverviewIcon />
        </button>
      </div>
      <div className={`tree__head ${focusedId === 'workspace' ? 'is-active' : ''}`}>
        {editingTitle ? (
          <input
            className="tree__head-input"
            defaultValue={projectTitle}
            autoFocus
            onBlur={e => renameProject(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') renameProject((e.target as HTMLInputElement).value)
              if (e.key === 'Escape') setEditingTitle(false)
            }}
            onClick={e => e.stopPropagation()}
          />
        ) : (
          <>
            <span className="tree__head-title" onClick={() => onFocus('workspace')}>
              {projectTitle}
            </span>
            <button className="tree__head-edit" title="Rename project"
                    onClick={e => { e.stopPropagation(); setEditingTitle(true) }}>✎</button>
            <button className="tree__head-overview" title="Project overview — all items, by status"
                    onClick={e => { e.stopPropagation(); onOpenOverview() }}>
              <OverviewIcon size={14} />
            </button>
          </>
        )}
      </div>

      <div className="tree__search">
        <svg width="13" height="13" viewBox="0 0 20 20" fill="currentColor" className="tree__search-icon">
          <path d="M9 3a6 6 0 014.5 9.9l3.3 3.3-1.4 1.4-3.3-3.3A6 6 0 119 3zm0 2a4 4 0 100 8 4 4 0 000-8z" />
        </svg>
        <input
          className="tree__search-input"
          placeholder="Search…"
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
        {query && (
          <button className="tree__search-clear" onClick={() => setQuery('')} title="Clear">×</button>
        )}
      </div>

      <div className="tree__scroll">
        {activeSection === 'files' ? (
          <FilesView focusedId={focusedId} onFocus={onFocus} onViewFile={onViewFile} reloadKey={entities.length} />
        ) : activeSection === 'threads' ? (() => {
          const threadList = [
            { id: 'default', title: 'Main thread', q: '', lifecycle: 'open' },
            ...threads.map(t => ({
              id: t.id, title: t.title, q: (t.metadata?.question as string) || '',
              lifecycle: (t.metadata?.lifecycle as string) || 'open',
            })),
          ]
          const threadFilter = sectionFilters.threads
          const filteredThreads = threadList.filter(t => threadMatchesFilter(t, threadFilter))
          const showKey = `threads:${threadFilter}`
          const tExpanded = !!showAll[showKey]
          const tShown = tExpanded ? filteredThreads : filteredThreads.slice(0, SECTION_CAP)
          return (
            <section className="tree__index tree__index--threads">
              <div className="tree__index-head">
                <div className="tree__title-row">
                  <span className="tree__tab-badge">
                    <RailIcon name="threads" size={17} />
                    Threads
                    <span className="tree__pill tree__pill--green">{threadList.length}</span>
                  </span>
                  <button className="tree__add-button" title="New investigation" onClick={newThread}>+</button>
                </div>
              </div>
              <div className="tree__filter-row" aria-label="Thread filters">
                {['Active', 'Attention', 'All'].map(filter => (
                  <button key={filter} className={threadFilter === filter ? 'is-on' : ''} onClick={() => selectFilter('threads', filter)}>
                    {filter}
                  </button>
                ))}
              </div>
              <div className="tree__index-list">
                <div className="tree__section-label">{threadFilter} questions</div>
                {tShown.map(t => {
                  const claimCount = countForThread(t.id, e => e.type === 'claim')
                  const runCount = countForThread(t.id, e => e.type === 'analysis')
                  const pinnedCount = countForThread(t.id, e => !!e.pinned)
                  const isCurrent = currentThread === t.id
                  return (
                    <div
                      key={t.id}
                      className={`tree__index-row ${isCurrent ? 'is-current' : ''} ${t.lifecycle !== 'open' ? `is-${t.lifecycle}` : ''}`}
                      onClick={() => onSelectThread(t.id)}
                    >
                      <button className="tree__index-main" title={t.q}>
                        <span className="tree__index-title">{t.title}</span>
                        <span className="tree__index-meta">
                          {t.lifecycle !== 'open' && <span className="state-tag state-tag--muted">{t.lifecycle}</span>}
                          {claimCount > 0 && <span>{claimCount} {claimCount === 1 ? 'claim' : 'claims'}</span>}
                          {pinnedCount > 0 && <span>{pinnedCount} pinned</span>}
                          {runCount > 0 && <span>{runCount} {runCount === 1 ? 'run' : 'runs'}</span>}
                          {!claimCount && !runCount && !pinnedCount && t.q && <span>question brief</span>}
                        </span>
                      </button>
                      <button
                        className="tree__overview-button"
                        title="Thread overview — all items by status"
                        onClick={e => { e.stopPropagation(); onOpenThreadOverview(t.id) }}
                      >
                        <OverviewIcon />
                      </button>
                    </div>
                  )}
                )}
                {filteredThreads.length === 0 && <div className="tree__empty">No questions match this filter.</div>}
                {filteredThreads.length > SECTION_CAP && (
                  <button className="tree__more"
                          onClick={() => setShowAll(s => ({ ...s, [showKey]: !s[showKey] }))}>
                    {tExpanded ? 'Show less' : `+${filteredThreads.length - SECTION_CAP} more`}
                  </button>
                )}
              </div>
            </section>
          )
        })() : (() => {
          const section = SECTION_CONFIG[activeSection]
          const items = visible.filter(e => section.types.includes(e.type))
          const sectionFilter = sectionFilters[activeSection]
          const filteredItems = items.filter(e => entityMatchesFilter(e, activeSection, sectionFilter))
          const showKey = `${activeSection}:${sectionFilter}`
          const expanded = !!showAll[showKey]
          const shown = expanded ? filteredItems : filteredItems.slice(0, SECTION_CAP)
          return (
            <section className={`tree__index tree__index--${activeSection}`}>
              <div className="tree__index-head">
                <div className="tree__title-row">
                  <span className="tree__tab-badge">
                    <RailIcon name={section.icon} size={17} />
                    {section.label}
                    <span className="tree__pill tree__pill--green">{items.length}</span>
                  </span>
                </div>
              </div>
              <div className="tree__filter-row" aria-label={`${section.label} filters`}>
                {section.filters.map(filter => (
                  <button key={filter} className={sectionFilter === filter ? 'is-on' : ''} onClick={() => selectFilter(activeSection, filter)}>
                    {filter}
                  </button>
                ))}
              </div>
              <div className="tree__index-list">
                <div className="tree__section-label">{sectionFilter === 'All' ? section.sectionLabel : `${sectionFilter} ${section.label.toLowerCase()}`}</div>
                {filteredItems.length === 0 ? (
                  <div className="tree__empty">{items.length === 0 ? section.empty : 'No items match this filter.'}</div>
                ) : shown.map(e => (
                  <div
                    key={e.id}
                    className={`tree__index-row tree__index-row--entity ${focusedId === e.id ? 'is-current' : ''} ${e.status === 'failed' || e.status === 'running' ? 'is-warning' : ''}`}
                    onClick={() => onFocus(e.id)}
                    data-entity-id={e.id}
                    data-entity-type={e.type}
                  >
                    <button className="tree__index-main">
                      <span className="tree__index-title">{e.title}</span>
                      <span className="tree__index-meta">{entityMeta(e)}</span>
                    </button>
                    <EntityMenu entity={e} onChange={onChange} />
                  </div>
                ))}
                {filteredItems.length > SECTION_CAP && (
                  <button className="tree__more"
                          onClick={() => setShowAll(s => ({ ...s, [showKey]: !s[showKey] }))}>
                    {expanded ? 'Show less' : `+${filteredItems.length - SECTION_CAP} more`}
                  </button>
                )}
              </div>
            </section>
          )
        })()}
      </div>

      <div className="tree__footer">
        <label className="tree__toggle">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={e => setShowArchived(e.target.checked)}
          />
          show archived
        </label>
      </div>
    </aside>
  )
}
