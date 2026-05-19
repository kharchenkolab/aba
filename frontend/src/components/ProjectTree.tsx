import './ProjectTree.css'
import type { Entity, EntityType } from '../types'

interface Props {
  entities: Entity[]
  focusedId: string
  onFocus: (id: string) => void
}

// Tree section spec: which entity types belong, and a presentation label.
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
    label: 'Findings',
    types: ['result', 'finding'],
    iconPath: 'M10 3a7 7 0 100 14 7 7 0 000-14zm-1 11l-3-3 1.4-1.4L9 11.2l4.6-4.6L15 8l-6 6z',
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

function TreeItem({
  entity,
  focused,
  onClick,
  indent = 0,
}: {
  entity: Entity
  focused: boolean
  onClick: () => void
  indent?: number
}) {
  return (
    <div
      className={`tree__item ${focused ? 'is-active' : ''}`}
      style={{ paddingLeft: 14 + indent * 14 }}
      onClick={onClick}
      data-entity-id={entity.id}
      data-entity-type={entity.type}
    >
      <svg className="icon" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
        <path d={TYPE_ICONS[entity.type]} />
      </svg>
      <span className="tree__item-label">
        {entity.title}
        <span className="meta">
          <span className="dot" />
          {entity.type}
        </span>
      </span>
    </div>
  )
}

export default function ProjectTree({ entities, focusedId, onFocus }: Props) {
  const byParent: Record<string, Entity[]> = {}
  for (const e of entities) {
    const k = e.parent_entity_id ?? ''
    ;(byParent[k] ??= []).push(e)
  }

  const workspace = entities.find(e => e.id === 'workspace')

  return (
    <aside className="tree">
      <div
        className={`tree__head ${focusedId === 'workspace' ? 'is-active' : ''}`}
        onClick={() => onFocus('workspace')}
      >
        {workspace?.title ?? 'Workspace'}
        <svg className="chev" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
          <path d="M5 8l5 5 5-5z" />
        </svg>
      </div>

      {SECTIONS.map(section => {
        const items = entities.filter(e => section.types.includes(e.type))
        return (
          <section key={section.label} className="tree__section">
            <div className="tree__section-head open">
              <svg className="icon" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
                <path d={section.iconPath} />
              </svg>
              {section.label}
              <span className="tree__count">{items.length || ''}</span>
              <svg className="chev ml-auto" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
                <path d="M5 8l5 5 5-5z" />
              </svg>
            </div>
            {items.length > 0 && (
              <div className="tree__items">
                {items.map(e => (
                  <TreeItem
                    key={e.id}
                    entity={e}
                    focused={focusedId === e.id}
                    onClick={() => onFocus(e.id)}
                  />
                ))}
              </div>
            )}
          </section>
        )
      })}
    </aside>
  )
}
