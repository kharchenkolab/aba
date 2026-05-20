import { useState, useEffect } from 'react'
import './ProjectTree.css'
import type { Entity, EntityType } from '../types'
import EntityMenu from './EntityMenu'

interface Props {
  entities: Entity[]
  focusedId: string
  onFocus: (id: string) => void
  onChange: () => void
}

const SECTIONS: { label: string; types: EntityType[]; iconPath: string }[] = [
  {
    label: 'Data',
    types: ['dataset'],
    iconPath: 'M3 5a2 2 0 012-2h10a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V5z',
  },
  {
    label: 'Analyses',
    types: ['analysis'],
    iconPath: 'M3 12l3-6 4 8 3-5 4 7H3z',
  },
  {
    label: 'Figures',
    types: ['figure', 'table'],
    iconPath: 'M3 4h14v12H3zM5 7h10v2H5zM5 11h6v2H5z',
  },
  {
    label: 'Results',
    types: ['result'],
    iconPath: 'M10 3a7 7 0 100 14 7 7 0 000-14zm-1 11l-3-3 1.4-1.4L9 11.2l4.6-4.6L15 8l-6 6z',
  },
  {
    label: 'Findings',
    types: ['finding'],
    iconPath: 'M10 2L5 7v6l5 5 5-5V7l-5-5zm0 2.4L13.6 8H6.4L10 4.4z',
  },
  {
    label: 'Manuscript',
    types: ['claim', 'narrative'],
    iconPath: 'M6 2h8l4 4v12a2 2 0 01-2 2H4a2 2 0 01-2-2V4a2 2 0 012-2z',
  },
]

const TYPE_ICONS: Record<EntityType, string> = {
  workspace: '',
  dataset: 'M4 4a2 2 0 00-2 2v8a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2H4z',
  analysis: 'M3 12l3-6 4 8 3-5 4 7H3z',
  figure: 'M3 4h14v12H3zM5 7h10v2H5zM5 11h6v2H5z',
  table: 'M3 4h14v3H3zM3 9h14v3H3zM3 14h14v3H3z',
  result: 'M10 3a7 7 0 100 14 7 7 0 000-14z',
  finding: 'M10 3a7 7 0 100 14 7 7 0 000-14z',
  claim: 'M5 3h10v14H5z',
  narrative: 'M5 3h10v14H5z',
}

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
}

function TreeItem({ entity, focused, onClick, onChange }: TreeItemProps) {
  const statusIcon = STATUS_ICON[entity.status]
  return (
    <div
      className={`tree__item ${focused ? 'is-active' : ''} ${entity.status === 'archived' ? 'is-archived' : ''}`}
      onClick={onClick}
      data-entity-id={entity.id}
      data-entity-type={entity.type}
    >
      <svg className="icon" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
        <path d={TYPE_ICONS[entity.type]} />
      </svg>
      <span className="tree__item-label">
        <span className="tree__title-line">
          {entity.pinned && <span className="tree__pin">★</span>}
          {entity.title}
        </span>
        <span className="meta">
          {statusIcon && <span className={`tree__status tree__status--${entity.status}`}>{statusIcon}</span>}
          <span className="dot" />
          {entity.type}
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

export default function ProjectTree({ entities, focusedId, onFocus, onChange }: Props) {
  const [query, setQuery] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [collapsed, setCollapsedState] = useState<Record<string, boolean>>(() => loadCollapsed())

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
  const pinned = visible.filter(e => e.pinned)
  const projectTitle = workspace?.title ?? 'Workspace'

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

      {pinned.length > 0 && (
        <section className="tree__section">
          <div className="tree__section-head open">
            <svg className="icon" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10 2l2.5 5 5.5.8-4 3.9.9 5.4L10 14.8 5.1 17l.9-5.4-4-3.9 5.5-.8L10 2z" />
            </svg>
            Pinned
            <span className="tree__count">{pinned.length}</span>
          </div>
          <div className="tree__items">
            {pinned.map(e => (
              <TreeItem
                key={e.id}
                entity={e}
                focused={focusedId === e.id}
                onClick={() => onFocus(e.id)}
                onChange={onChange}
              />
            ))}
          </div>
        </section>
      )}

      {SECTIONS.map(section => {
        const items = visible.filter(e => section.types.includes(e.type) && !e.pinned)
        const isCollapsed = !!collapsed[section.label]
        return (
          <section key={section.label} className="tree__section">
            <div
              className={`tree__section-head ${isCollapsed ? '' : 'open'}`}
              onClick={() => toggle(section.label)}
            >
              <svg className="icon" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
                <path d={section.iconPath} />
              </svg>
              {section.label}
              <span className="tree__count">{items.length || ''}</span>
              <svg className="chev ml-auto" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
                <path d={isCollapsed ? 'M8 5l5 5-5 5z' : 'M5 8l5 5 5-5z'} />
              </svg>
            </div>
            {!isCollapsed && items.length > 0 && (
              <div className="tree__items">
                {items.map(e => (
                  <TreeItem
                    key={e.id}
                    entity={e}
                    focused={focusedId === e.id}
                    onClick={() => onFocus(e.id)}
                    onChange={onChange}
                  />
                ))}
              </div>
            )}
          </section>
        )
      })}

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
