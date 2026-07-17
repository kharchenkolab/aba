import { useState } from 'react'
import type { ReactNode } from 'react'
import './ProjectTree.css'
import type { Entity, EntityType } from '../types'
import EntityMenu from './EntityMenu'
import LedgerStrip from './LedgerStrip'
import SearchInput from '../components/SearchInput'
import { RailIcon, type RailIconName } from '../components/icons'
import FilesView from './FilesView'
import UploadDrop, { walkDropEntries, uploadWalkedAppend } from '../platform/UploadDrop'
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
  /** Files tab deep-link target (e.g. a Run's output folder). */
  filesTarget?: { path: string; n: number }
  /** Pin per-request so FilesView's /api/files/tree isn't subject to the
   *  backend's in-process current-project state. */
  projectId?: string
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
    // Under the unified Pin/Result model (misc/entity_pin_redesign.md), Results
    // ARE the curated layer; figures/tables/notes/narratives live as evidence
    // INSIDE Results (or under their Run, or in Files). The Results tab and the
    // PinnedShelf show the same thing.
    types: ['result'],
    icon: 'results',
    empty: 'No results yet — Pin a figure or table in chat to create one.',
    sectionLabel: 'Project results',
    filters: ['All'],
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

// State-filter pills (Active / Contested / All etc.) were removed
// 2026-06-05 — every section now defaults to 'All' so nothing is
// hidden by an implicit state filter the user can no longer toggle.
// Archived items are still gated by the separate showArchived toggle.
const DEFAULT_FILTERS: Record<ProjectSection, string> = {
  threads: 'All',
  claims: 'All',
  data: 'All',
  runs: 'All',
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
const SEARCH_MIN = 5    // a section reveals its filter box once it holds this many items (5 or more — PK 2026-06-05)

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

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

export default function ProjectTree({ entities, focusedId, activeSection, onFocus, onViewFile, onChange, currentThread, onSelectThread, onOpenOverview, onOpenThreadOverview, filesTarget, projectId }: Props) {
  const [query, setQuery] = useState('')
  // The filter box is per-tab: reset its text when the active section changes so
  // a filter typed on one list never silently carries into another. Done during
  // render (the React "reset state on prop change" pattern) to avoid a one-frame
  // flash of the previous query's results.
  const [prevSection, setPrevSection] = useState(activeSection)
  if (activeSection !== prevSection) { setPrevSection(activeSection); setQuery('') }
  const [showArchived, setShowArchived] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [showAll, setShowAll] = useState<Record<string, boolean>>({})
  const [uploadOpen, setUploadOpen] = useState(false)
  const [sectionFilters] = useState<Record<ProjectSection, string>>(DEFAULT_FILTERS)
  // Inline rename state for newly-created datasets (Datasets `+` flow).
  // Reused by any entity row that opts into rename (F5 polish).
  const [renamingId, setRenamingId] = useState<string | null>(null)
  // Per-row drag-over highlight + in-flight upload spinner for the
  // experimental rail-row drop landing pad (drag a file → append to dataset).
  const [dropTargetId, setDropTargetId] = useState<string | null>(null)
  const [appendingIds, setAppendingIds] = useState<Set<string>>(() => new Set())

  const workspace = entities.find(e => e.id === 'workspace')

  // Apply search + archived filter client-side. Server-side same path will
  // be used when projects exceed ~hundreds of entities.
  const q = query.trim().toLowerCase()
  const archivedOk = (e: Entity) => showArchived || e.status !== 'archived'
  // How many items the active section holds, independent of the search box, so
  // the box's >5 reveal never flickers as the query narrows the list. The Files
  // tab carries its own search (in <FileBrowser>), so it's excluded here.
  const sectionTotal = (section: ProjectSection): number => {
    if (section === 'files') return 0
    if (section === 'threads') {
      return 1 + entities.filter(e => e.type === 'thread' && !e.metadata?.is_default && archivedOk(e)).length
    }
    const types = SECTION_CONFIG[section].types
    return entities.filter(e =>
      e.id !== 'workspace' && e.status !== 'superseded' && archivedOk(e) &&
      types.includes(e.type) && !(e.metadata as { ambient?: boolean } | undefined)?.ambient).length
  }
  // The filter box appears only once the list is long enough to warrant it.
  // Once shown it stays shown (the threshold reads the UNFILTERED count), and
  // the query only bites while the box is actually visible — so navigating to a
  // short section never leaves a hidden filter silently trimming it.
  const searchEligible = sectionTotal(activeSection) >= SEARCH_MIN
  const effectiveQ = searchEligible ? q : ''
  const filterFn = (e: Entity) => {
    if (e.id === 'workspace') return false
    // Superseded figures are version history — only the latest shows in the
    // tree; older versions live in the figure's history drawer.
    if (e.status === 'superseded') return false
    if (!showArchived && e.status === 'archived') return false
    if (effectiveQ && !e.title.toLowerCase().includes(effectiveQ)) return false
    return true
  }
  const visible = entities.filter(filterFn)
  const byId = new Map(entities.map(e => [e.id, e]))
  // Most-recent first — the backend default sort is created_at ASC, but the
  // rail caps at SECTION_CAP (8) so without flipping here, the user only
  // ever sees the OLDEST items at the top of every list (PK 2026-06-09).
  const byRecent = (a: Entity, b: Entity) =>
    (b.created_at || '').localeCompare(a.created_at || '')
  // Named threads only; the default thread is represented by the "Main thread"
  // chip (selects the 'default' alias), not a duplicate named row. Respects
  // showArchived so the "show archived" footer checkbox actually surfaces
  // archived threads in the Threads section (PK 2026-06-02: the toggle was
  // appearing but doing nothing because this list pre-excluded archived).
  const threads = entities.filter(e => e.type === 'thread' && !e.metadata?.is_default
                                       && (showArchived || e.status !== 'archived'))
                          .sort(byRecent)
  const hasArchived = entities.some(e => e.status === 'archived' && !e.metadata?.is_default)
  const projectTitle = workspace?.title ?? 'Workspace'

  async function newThread() {
    const r = await fetch('/api/threads', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'New investigation' }),
    })
    if (r.ok) { const t = await r.json(); onChange(); onSelectThread(t.id) }
  }

  async function createEmptyDataset() {
    const r = await fetch('/api/datasets', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, name: 'New dataset' }),
    })
    if (!r.ok) return
    const e = await r.json()
    onChange()
    onFocus(e.id)
    setRenamingId(e.id)
  }

  async function handleRowDrop(e: React.DragEvent, dataset: Entity) {
    // Only directory-shaped datasets accept appends — backend enforces this,
    // but skip silently here so single-file dataset rows don't act as targets.
    if (dataset.metadata?.layout !== 'directory') {
      setDropTargetId(null)
      return
    }
    e.preventDefault(); e.stopPropagation()
    setDropTargetId(null)
    const walked = await walkDropEntries(e.dataTransfer)
    if (walked.length === 0) return
    setAppendingIds(s => new Set(s).add(dataset.id))
    try {
      await uploadWalkedAppend(walked, dataset.id, projectId)
    } finally {
      setAppendingIds(s => { const n = new Set(s); n.delete(dataset.id); return n })
      onChange()
    }
  }

  async function commitRename(id: string, value: string) {
    setRenamingId(null)
    const t = value.trim()
    if (!t) return
    const ent = entities.find(x => x.id === id)
    if (ent && ent.title === t) return
    await fetch(`/api/entities/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: t }),
    })
    onChange()
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
      const fc = entity.metadata?.file_count as number | undefined
      const bytes = entity.metadata?.size_bytes as number | undefined
      if (typeof fc === 'number') {
        const fileLine = fc === 0
          ? <span key="files" className="tree__index-files--empty">empty</span>
          : <span key="files">{fc} {fc === 1 ? 'file' : 'files'}{typeof bytes === 'number' ? ` · ${formatBytes(bytes)}` : ''}</span>
        meta.push(fileLine)
      }
      const description = firstText(entity.metadata?.description, entity.metadata?.summary, entity.metadata?.text, entity.notes, entity.metadata?.source, entity.metadata?.path)
      // Keep the tree line short — the full text lives in the center detail view.
      if (description) meta.push(<span key="description">{description.length > 80 ? description.slice(0, 80).trimEnd() + '…' : description}</span>)
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
    // Pinned-tag dropped — pinning is now expressed as an active Result
    // entity that wraps the figure/table (task #318). The bare entity has
    // no `pinned` flag anymore; the Result IS the indicator.
    entity.tags.slice(0, 2).forEach(tag => meta.push(<span key={tag}>{tag}</span>))
    return meta
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
    // Results section no longer has Figures/Tables sub-filters (Results are the
    // unified curation layer; figures/tables aren't Results — they're evidence
    // *inside* Results, viewable under their Run or in Files).
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

      {activeSection === 'files' ? (
        <FilesView focusedId={focusedId} onFocus={onFocus} onViewFile={onViewFile} reloadKey={entities.length}
                   targetPath={filesTarget?.path} targetNonce={filesTarget?.n} projectId={projectId} />
      ) : (
      <div className="tree__scroll">
        {activeSection === 'threads' ? (() => {
          const threadList = [
            { id: 'default', title: 'Main thread', q: '', lifecycle: 'open', entity: null as Entity | null },
            ...threads.map(t => ({
              id: t.id, title: t.title, q: (t.metadata?.question as string) || '',
              lifecycle: (t.metadata?.lifecycle as string) || 'open',
              entity: t as Entity,
            })),
          ]
          const threadFilter = sectionFilters.threads
          const filteredThreads = threadList.filter(t =>
            threadMatchesFilter(t, threadFilter) &&
            (!effectiveQ || t.title.toLowerCase().includes(effectiveQ)))
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
              {/* State-filter pills (Active/Attention/All) removed
                  2026-06-05 per PK: not enough payoff at typical list
                  sizes; the per-section search box does the real
                  narrowing. Filter machinery kept (threadMatchesFilter
                  + sectionFilters state) so re-introducing a saved-view
                  affordance later is one component, not a refactor. */}
              {searchEligible && (
                <SearchInput value={query} onChange={setQuery}
                             placeholder="Filter threads…" ariaLabel="Filter threads by name" />
              )}
              <div className="tree__index-list">
                <div className="tree__section-label">{threadFilter} questions</div>
                {tShown.map(t => {
                  const claimCount = countForThread(t.id, e => e.type === 'claim')
                  const runCount = countForThread(t.id, e => e.type === 'analysis' && !(e.metadata as { ambient?: boolean } | undefined)?.ambient)
                  // "Pinned" count = active Result entities in the thread
                  // (the wrapper created when the user pins something).
                  const pinnedCount = countForThread(t.id, e => e.type === 'result' && e.status === 'active')
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
                      {t.entity && (
                        <span onClick={e => e.stopPropagation()}>
                          <EntityMenu entity={t.entity} onChange={onChange} />
                        </span>
                      )}
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
          // Ambient Runs (lifecycle/registry.py:_ensure_analysis) are catch-all
          // analyses created to parent pre-plan ad-hoc outputs. They're structural
          // bookkeeping — never user-facing. The producing code's own comment says
          // "HIDDEN from the Runs UI"; this is that filter.
          const items = visible.filter(e =>
            section.types.includes(e.type) && !(e.metadata as { ambient?: boolean } | undefined)?.ambient)
            .sort(byRecent)
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
                    <span className="tree__pill tree__pill--green">{sectionTotal(activeSection)}</span>
                  </span>
                  {activeSection === 'data' && (
                    <button className="tree__add-button" title="Create a new dataset"
                            onClick={createEmptyDataset}>+</button>
                  )}
                </div>
              </div>
              {/* §1 safety ledger — self-quieting: renders nothing when every
                  item is safe and local (the local-only snapshot contract). */}
              {(activeSection === 'data' || activeSection === 'results') && (
                <LedgerStrip projectId={projectId} onFocus={onFocus} />
              )}
              {/* State-filter pills removed (see Threads section above). */}
              {searchEligible && (
                <SearchInput value={query} onChange={setQuery}
                             placeholder={`Filter ${section.label.toLowerCase()}…`}
                             ariaLabel={`Filter ${section.label} by name`} />
              )}
              <div className="tree__index-list">
                <div className="tree__section-label">{sectionFilter === 'All' ? section.sectionLabel : `${sectionFilter} ${section.label.toLowerCase()}`}</div>
                {filteredItems.length === 0 ? (
                  <div className="tree__empty">{sectionTotal(activeSection) === 0 ? section.empty : 'No items match this filter.'}</div>
                ) : shown.map(e => {
                  // Rail-row drop landing pad is dataset-only (experimental).
                  const isDataset = e.type === 'dataset' && e.metadata?.layout === 'directory'
                  const isDropTarget = isDataset && dropTargetId === e.id
                  const isAppending = appendingIds.has(e.id)
                  return (
                  <div
                    key={e.id}
                    className={`tree__index-row tree__index-row--entity ${focusedId === e.id ? 'is-current' : ''} ${e.status === 'failed' || e.status === 'running' ? 'is-warning' : ''} ${isDropTarget ? 'is-drop-target' : ''} ${isAppending ? 'is-appending' : ''}`}
                    onClick={() => { if (renamingId !== e.id) onFocus(e.id) }}
                    data-entity-id={e.id}
                    data-entity-type={e.type}
                    onDragOver={isDataset ? (ev => {
                      // Only handle file drops; ignore react-dnd / internal drags.
                      if (!Array.from(ev.dataTransfer.types).includes('Files')) return
                      ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy'
                      setDropTargetId(e.id)
                    }) : undefined}
                    onDragLeave={isDataset ? (() => setDropTargetId(t => t === e.id ? null : t)) : undefined}
                    onDrop={isDataset ? (ev => handleRowDrop(ev, e)) : undefined}
                  >
                    {renamingId === e.id ? (
                      <input
                        className="tree__index-rename"
                        defaultValue={e.title}
                        autoFocus
                        onFocus={ev => ev.currentTarget.select()}
                        onClick={ev => ev.stopPropagation()}
                        onBlur={ev => commitRename(e.id, ev.target.value)}
                        onKeyDown={ev => {
                          if (ev.key === 'Enter') (ev.target as HTMLInputElement).blur()
                          if (ev.key === 'Escape') { setRenamingId(null); (ev.target as HTMLInputElement).blur() }
                        }}
                      />
                    ) : (
                      <button className="tree__index-main">
                        <span
                          className="tree__index-title"
                          title={e.type === 'dataset' ? 'Double-click to rename' : undefined}
                          onDoubleClick={e.type === 'dataset' ? (ev => {
                            ev.stopPropagation(); ev.preventDefault()
                            setRenamingId(e.id)
                          }) : undefined}
                        >{e.title}</span>
                        <span className="tree__index-meta">{entityMeta(e)}</span>
                      </button>
                    )}
                    {isAppending && <span className="tree__index-spinner" title="Uploading…">⟳</span>}
                    <EntityMenu entity={e} onChange={onChange} />
                  </div>
                  )
                })}
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
      )}

      {(hasArchived || showArchived) && (
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
      )}
      {uploadOpen && (
        <UploadDrop onClose={() => setUploadOpen(false)} onUploaded={onChange} />
      )}
    </aside>
  )
}
