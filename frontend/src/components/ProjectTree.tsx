import { useState, useEffect } from 'react'
import './ProjectTree.css'
import type { Entity, EntityType } from '../types'
import EntityMenu from './EntityMenu'
import { EntityGlyph } from './icons'

interface Props {
  entities: Entity[]
  focusedId: string
  onFocus: (id: string) => void
  onChange: () => void
  currentThread: string
  onSelectThread: (id: string) => void
  onOpenOverview: () => void
  onOpenThreadOverview: (id: string) => void
}

// Rail top-level sections, per ui3 P8: Datasets · Threads · Claims · Manuscript.
// (Threads have their own switcher above.) Figures/tables are thread artifacts —
// they live in the per-thread pinned shelf + inventory, not as a rail dump.
// Legacy `result`/`finding` types were dropped in entity-model v3. Every section
// below is hidden when empty (progressive disclosure — a fresh project shows
// almost nothing).
const SECTIONS: { label: string; types: EntityType[]; icon: string }[] = [
  // Inquiry first (Threads has its own switcher above), then Claims, then data
  // last — mirrors the project-overview column order.
  { label: 'Claims', types: ['claim'], icon: 'claim' },
  { label: 'Manuscript', types: ['narrative'], icon: 'narrative' },
  { label: 'Data', types: ['dataset'], icon: 'dataset' },
]

const STATUS_ICON: Record<string, string> = {
  running: '▶',
  superseded: '↺',
  failed: '⚠',
  archived: '📦',
}

const COLLAPSE_KEY = 'aba:tree-collapsed'

function loadCollapsed(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '{}')
  } catch {
    return {}
  }
}

function saveCollapsed(state: Record<string, boolean>) {
  try {
    localStorage.setItem(COLLAPSE_KEY, JSON.stringify(state))
  } catch {
    /* quota / disabled storage — fine */
  }
}

interface TreeItemProps {
  entity: Entity
  focused: boolean
  onClick: () => void
  onChange: () => void
  inPinned?: boolean
}

function TreeItem({ entity, focused, onClick, onChange, inPinned }: TreeItemProps) {
  const statusIcon = STATUS_ICON[entity.status]
  return (
    <div
      className={`tree__item ${focused ? 'is-active' : ''} ${entity.status === 'archived' ? 'is-archived' : ''}`}
      onClick={onClick}
      data-entity-id={entity.id}
      data-entity-type={entity.type}
    >
      <EntityGlyph className="icon" name={entity.type} />
      <span className="tree__item-label">
        <span className="tree__title-line">
          {/* Red pin marks a pinned entity (matches the chat pin); redundant
              inside the Pinned panel. */}
          {entity.pinned && !inPinned && (
            <svg className="tree__pin" width="11" height="11" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" strokeWidth="2"><path d="M12 17v5M9 3h6l-1 7 3 3H7l3-3z"/></svg>
          )}
          <span className="tree__item-title">{entity.title}</span>
        </span>
        <span className="meta">
          {statusIcon && <span className={`tree__status tree__status--${entity.status}`}>{statusIcon}</span>}
          {entity.type === 'claim' && entity.metadata?.confidence ? (
            <span className={`tree__confidence tree__confidence--${entity.metadata.confidence}`}>{String(entity.metadata.confidence)}</span>
          ) : (
            <><span className="dot" />{entity.type}</>
          )}
          {entity.tags.length > 0 && (
            <>
              {entity.tags.slice(0, 2).map(t => (
                <span key={t} className="tree__tag">{t}</span>
              ))}
              {entity.tags.length > 2 && <span className="tree__tag-extra">+{entity.tags.length - 2}</span>}
            </>
          )}
        </span>
      </span>
      <EntityMenu entity={entity} onChange={onChange} />
    </div>
  )
}

const SECTION_CAP = 8   // items shown per section before "show all"

export default function ProjectTree({ entities, focusedId, onFocus, onChange, currentThread, onSelectThread, onOpenOverview, onOpenThreadOverview }: Props) {
  const [query, setQuery] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [collapsed, setCollapsedState] = useState<Record<string, boolean>>(() => loadCollapsed())
  const [showAll, setShowAll] = useState<Record<string, boolean>>({})

  useEffect(() => { saveCollapsed(collapsed) }, [collapsed])

  function toggle(section: string) {
    setCollapsedState(s => ({ ...s, [section]: !s[section] }))
  }

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

  return (
    <aside className="tree">
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
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"><rect x="3" y="4" width="5" height="16" rx="1"/><rect x="9.5" y="4" width="5" height="16" rx="1"/><rect x="16" y="4" width="5" height="16" rx="1"/></svg>
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
      {/* Thread switcher — the current line of inquiry scopes the chat. */}
      {(() => {
        const threadList = [
          { id: 'default', title: 'Main thread', q: '', lifecycle: 'open' },
          ...threads.map(t => ({
            id: t.id, title: t.title, q: (t.metadata?.question as string) || '',
            lifecycle: (t.metadata?.lifecycle as string) || 'open',
          })),
        ]
        const tCollapsed = !!collapsed['Threads']
        const tExpanded = !!showAll['Threads']
        const tShown = tExpanded ? threadList : threadList.slice(0, SECTION_CAP)
        return (
          <section className="tree__section tree__threads">
            <div className="tree__section-head open" onClick={() => toggle('Threads')}>
              <svg className="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 5h16M4 12h16M4 19h10"/></svg>
              Threads
              <span className="tree__count">{threadList.length}</span>
              <button className="tree__new-thread" title="New investigation"
                      onClick={e => { e.stopPropagation(); newThread() }}>+</button>
              <svg className="chev" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
                <path d={tCollapsed ? 'M8 5l5 5-5 5z' : 'M5 8l5 5 5-5z'} />
              </svg>
            </div>
            {!tCollapsed && (
              <div className="tree__items">
                {tShown.map(t => (
                  <div key={t.id} className={`tree__thread-row ${currentThread === t.id ? 'is-current' : ''} ${t.lifecycle !== 'open' ? 'is-' + t.lifecycle : ''}`}>
                    <button className="tree__thread" onClick={() => onSelectThread(t.id)} title={t.q}>{t.title}</button>
                    {t.lifecycle !== 'open' && <span className={`tree__lc tree__lc--${t.lifecycle}`}>{t.lifecycle === 'concluded' ? 'done' : 'parked'}</span>}
                    <button className="tree__thread-ov" title="Thread overview — all items by status"
                            onClick={e => { e.stopPropagation(); onOpenThreadOverview(t.id) }}>
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"><rect x="3" y="4" width="5" height="16" rx="1"/><rect x="9.5" y="4" width="5" height="16" rx="1"/><rect x="16" y="4" width="5" height="16" rx="1"/></svg>
                    </button>
                  </div>
                ))}
                {threadList.length > SECTION_CAP && (
                  <button className="tree__more"
                          onClick={() => setShowAll(s => ({ ...s, Threads: !s.Threads }))}>
                    {tExpanded ? 'Show less' : `+${threadList.length - SECTION_CAP} more`}
                  </button>
                )}
              </div>
            )}
          </section>
        )
      })()}

      {/* The flat "Pinned" tray is superseded by the per-thread pinned shelf
          (the chat-first right peek); pinned items live under their thread. */}

      {SECTIONS.map(section => {
        const items = visible.filter(e => section.types.includes(e.type))
        // Progressive disclosure: a section with nothing in it doesn't exist yet.
        if (items.length === 0) return null
        const isCollapsed = !!collapsed[section.label]
        const expanded = !!showAll[section.label]
        const shown = expanded ? items : items.slice(0, SECTION_CAP)
        return (
          <section key={section.label} className="tree__section">
            <div
              className={`tree__section-head ${isCollapsed ? '' : 'open'}`}
              onClick={() => toggle(section.label)}
            >
              <EntityGlyph className="icon" name={section.icon} />
              {section.label}
              <span className="tree__count">{items.length || ''}</span>
              <svg className="chev ml-auto" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
                <path d={isCollapsed ? 'M8 5l5 5-5 5z' : 'M5 8l5 5 5-5z'} />
              </svg>
            </div>
            {!isCollapsed && items.length > 0 && (
              <div className="tree__items">
                {shown.map(e => (
                  <TreeItem
                    key={e.id}
                    entity={e}
                    focused={focusedId === e.id}
                    onClick={() => onFocus(e.id)}
                    onChange={onChange}
                  />
                ))}
                {items.length > SECTION_CAP && (
                  <button className="tree__more"
                          onClick={() => setShowAll(s => ({ ...s, [section.label]: !s[section.label] }))}>
                    {expanded ? 'Show less' : `+${items.length - SECTION_CAP} more`}
                  </button>
                )}
              </div>
            )}
          </section>
        )
      })}
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
