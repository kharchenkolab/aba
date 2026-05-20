/**
 * Skills catalog (Phase 12, minimal). Lists tools + skills Guide can drive,
 * grouped by category, searchable. Reached from the left-rail Skills icon.
 *
 * Toggle / BioMNI integration / "Run this" entry-point will land when the
 * tool surface grows.
 */
import { useEffect, useMemo, useState } from 'react'
import './Skills.css'

interface Item {
  kind: 'tool' | 'skill' | 'module'
  name: string
  category: string
  summary: string
  example?: string
  description?: string
  input_schema?: unknown
  knowhow_doc?: string
  enabled?: boolean
  toggleable?: boolean
}

interface Category {
  name: string
  items: Item[]
}

interface Catalog {
  categories: Category[]
  total: number
}

interface Props {
  onClose: () => void
}

export default function Skills({ onClose }: Props) {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Item | null>(null)

  function reload() {
    fetch('/api/tools')
      .then(r => (r.ok ? r.json() : Promise.reject(r)))
      .then(setCatalog)
      .catch(() => {})
  }
  useEffect(() => { reload() }, [])

  async function toggle(item: Item) {
    await fetch(`/api/tools/${encodeURIComponent(item.name)}/enabled`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !item.enabled }),
    })
    reload()
  }

  const filtered = useMemo(() => {
    if (!catalog) return null
    const q = query.trim().toLowerCase()
    if (!q) return catalog
    const cats: Category[] = []
    for (const cat of catalog.categories) {
      const items = cat.items.filter(
        i =>
          i.name.toLowerCase().includes(q) ||
          i.summary.toLowerCase().includes(q) ||
          cat.name.toLowerCase().includes(q),
      )
      if (items.length > 0) cats.push({ name: cat.name, items })
    }
    return { categories: cats, total: cats.reduce((s, c) => s + c.items.length, 0) }
  }, [catalog, query])

  return (
    <div className="skills-backdrop" onClick={onClose}>
      <div className="skills" onClick={e => e.stopPropagation()}>
        <div className="skills__head">
          <h2>Skills</h2>
          <span className="skills__count">
            {filtered ? `${filtered.total} of ${catalog?.total ?? 0}` : '…'}
          </span>
          <input
            className="skills__search"
            placeholder="Search tools and skills…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            autoFocus
          />
          <button onClick={onClose} className="skills__close" title="Close">×</button>
        </div>

        <div className="skills__body">
          <div className="skills__list">
            {filtered?.categories.map(cat => (
              <section key={cat.name} className="skills__section">
                <h3>{cat.name}</h3>
                <div className="skills__items">
                  {cat.items.map(it => (
                    <div
                      key={it.name}
                      className={`skills__row ${selected?.name === it.name ? 'skills__row--active' : ''} ${it.enabled === false ? 'skills__row--off' : ''}`}
                      onClick={() => setSelected(it)}
                    >
                      <span className={`skills__kind skills__kind--${it.kind}`}>{it.kind}</span>
                      <span className="skills__name">{it.name}</span>
                      <span className="skills__summary">{it.summary}</span>
                      {it.toggleable && (
                        <button
                          className={`skills__toggle ${it.enabled ? 'is-on' : ''}`}
                          onClick={e => { e.stopPropagation(); toggle(it) }}
                          title={it.enabled ? 'Disable (hidden from the agent)' : 'Enable'}
                        >
                          <span className="skills__toggle-knob" />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            ))}
            {filtered && filtered.total === 0 && (
              <div className="skills__empty">No matches for &ldquo;{query}&rdquo;.</div>
            )}
          </div>

          <div className="skills__detail">
            {selected ? (
              <>
                <div className={`skills__detail-kind skills__kind--${selected.kind}`}>
                  {selected.kind}
                </div>
                <h3>{selected.name}</h3>
                <p className="skills__detail-summary">{selected.summary}</p>
                {selected.example && (
                  <div className="skills__example">
                    <div className="skills__label">Example ask</div>
                    <code>{selected.example}</code>
                  </div>
                )}
                {selected.knowhow_doc && (
                  <div className="skills__example">
                    <div className="skills__label">Reference</div>
                    <code>{selected.knowhow_doc}</code>
                  </div>
                )}
                {selected.input_schema != null && (
                  <details className="skills__schema">
                    <summary>JSON Schema</summary>
                    <pre>{JSON.stringify(selected.input_schema, null, 2)}</pre>
                  </details>
                )}
                {selected.description && selected.description !== selected.summary && (
                  <details className="skills__schema">
                    <summary>Full description</summary>
                    <p>{selected.description}</p>
                  </details>
                )}
              </>
            ) : (
              <div className="skills__detail-empty">
                Select a tool or skill to see details.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
