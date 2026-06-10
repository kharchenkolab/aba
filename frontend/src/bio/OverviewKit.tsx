/**
 * Shared overview primitives — used by both the Project overview (P8) and the
 * Thread overview. A column is a titled list of status groups; each group shows
 * a count and its top rows with "show all N". Rows are caller-supplied so each
 * overview can render entity rows or custom content (e.g. open questions).
 *
 * Columns may carry a header "+" (add an item) and rows a hover "⋯" menu — but
 * the primary gesture stays a single click on the row to navigate to the entity.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { EntityGlyph } from '../components/icons'
import './ProjectOverview.css'
import './EntityMenu.css'

export const TOP_N = 6

export type Tone = 'attention' | 'retired' | 'plain'

export interface OvGroup { label: string; tone?: Tone; rows: ReactNode[] }

export function OverviewColumn({ title, total, groups, emptyHint, onAdd, onAddText, addPlaceholder, addTitle }: {
  title: string; total: number; groups: OvGroup[]; emptyHint?: string
  onAdd?: () => void                       // "+" runs a callback (e.g. open a dialog)
  onAddText?: (text: string) => void       // "+" reveals an inline input; submit creates
  addPlaceholder?: string; addTitle?: string
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [adding, setAdding] = useState(false)
  const shownTotal = groups.reduce((n, g) => n + g.rows.length, 0)
  const onPlus = onAddText ? () => setAdding(a => !a) : onAdd
  return (
    <section className="ov-col">
      <header className="ov-col__head">
        <span className="ov-col__title">{title}</span>
        <span className="ov-col__count">{total}</span>
        {onPlus && (
          <button className="ov-col__add" title={addTitle ?? 'Add'} onClick={onPlus}>+</button>
        )}
      </header>
      {adding && onAddText && (
        <InlineAdd placeholder={addPlaceholder ?? 'Add…'}
                   onSubmit={t => { setAdding(false); if (t.trim()) onAddText(t.trim()) }}
                   onCancel={() => setAdding(false)} />
      )}
      {shownTotal === 0 && !adding && <div className="ov-col__empty">{emptyHint ?? 'Nothing here.'}</div>}
      {groups.map(g => {
        if (g.rows.length === 0) return null
        const open = !!expanded[g.label]
        const shown = open ? g.rows : g.rows.slice(0, TOP_N)
        return (
          <div key={g.label} className={`ov-group ov-group--${g.tone ?? 'plain'}`}>
            <div className="ov-group__head">{g.label}<span className="ov-group__n">{g.rows.length}</span></div>
            {shown}
            {g.rows.length > TOP_N && (
              <button className="ov-group__more"
                      onClick={() => setExpanded(s => ({ ...s, [g.label]: !open }))}>
                {open ? 'Show less' : `show all ${g.rows.length} ›`}
              </button>
            )}
          </div>
        )
      })}
    </section>
  )
}

export function OverviewRow({ icon, label, sub, thumb, badge, badgeTone, tone, onClick, title, menu, dot }: {
  icon: string; label: string; sub?: string; thumb?: string; badge?: string; badgeTone?: string;
  tone?: Tone; onClick?: () => void; title?: string; menu?: ReactNode; dot?: string
}) {
  // A div (not a button) so a nested menu button is valid HTML; click + keyboard
  // give it button semantics when navigable.
  return (
    <div
      className={`ov-row ${tone === 'retired' ? 'ov-row--retired' : ''} ${tone === 'attention' ? 'ov-row--attn' : ''} ${onClick ? '' : 'ov-row--static'}`}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={onClick ? (e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } }) : undefined}
      title={title ?? label}
    >
      {/* A status dot replaces the type glyph as the row's bullet. */}
      {dot
        ? <span className={`ov-row__dot ov-row__dot--${dot}`} />
        : thumb
          ? <img className="ov-row__thumb" src={thumb} alt="" loading="lazy" />
          : <EntityGlyph className="ov-row__icon" name={icon} size={13} />}
      <span className="ov-row__label">
        {label}
        {sub && <span className="ov-row__sub">{sub}</span>}
      </span>
      {badge && <span className={`ov-badge ov-badge--${badgeTone ?? badge}`}>{badge}</span>}
      {menu && <span className="ov-row__menu">{menu}</span>}
    </div>
  )
}

/** Inline text input shown when a column's "+" is clicked (intentional add). */
export function InlineAdd({ placeholder, onSubmit, onCancel, value }: {
  placeholder: string; onSubmit: (t: string) => void; onCancel: () => void; value?: string
}) {
  const [v, setV] = useState(value ?? '')
  return (
    <input className="ov-oq-add" autoFocus placeholder={placeholder} value={v}
           onChange={e => setV(e.target.value)}
           onBlur={() => (v.trim() ? onSubmit(v) : onCancel())}
           onKeyDown={e => { if (e.key === 'Enter') onSubmit(v); if (e.key === 'Escape') onCancel() }} />
  )
}

/** A two-path "add a result/dataset" dialog: upload a file, or describe it and
 *  hand the request to the Guide (switches to chat). */
export function AddResourceDialog({ title, describeLabel, onUpload, onAsk, onClose }: {
  title: string; describeLabel: string
  onUpload: (f: File) => void; onAsk: (text: string) => void; onClose: () => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [desc, setDesc] = useState('')
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal ov-add-modal" onClick={e => e.stopPropagation()}>
        <h2 className="modal__title">{title}</h2>
        <button className="ov-add-modal__upload" onClick={() => fileRef.current?.click()}>
          ⬆ Upload a file
        </button>
        <input ref={fileRef} type="file" style={{ display: 'none' }}
               onChange={e => { const f = e.target.files?.[0]; if (f) { onUpload(f); onClose() } }} />
        <div className="ov-add-modal__or">or</div>
        <div className="ov-add-modal__label">{describeLabel}</div>
        <textarea className="ov-add-modal__desc" autoFocus rows={3} value={desc}
                  placeholder="e.g. the GEO accession GSE12345, or a wet-lab Western blot for MUC2…"
                  onChange={e => setDesc(e.target.value)} />
        <div className="modal__actions">
          <button className="home__btn" onClick={onClose}>Cancel</button>
          <button className="home__btn home__btn--primary" disabled={!desc.trim()}
                  onClick={() => { onAsk(desc.trim()); onClose() }}>Ask Guide →</button>
        </div>
      </div>
    </div>
  )
}

/** A simple ⋯ actions menu for non-entity rows (e.g. open questions). Floats
 *  with fixed positioning so it isn't clipped by the column's scroll area;
 *  reuses the entity-menu styling. */
export function RowMenu({ items }: { items: { label: string; onClick: () => void; danger?: boolean }[] }) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const ref = useRef<HTMLDivElement>(null)
  function toggle() {
    if (!open && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect()
      setPos({ top: r.bottom + 4, left: Math.max(8, r.right - 180) })
    }
    setOpen(v => !v)
  }
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])
  return (
    <div className="entity-menu" ref={ref} onClick={e => e.stopPropagation()}>
      <button ref={btnRef} className="entity-menu__btn" title="Actions"
              onClick={e => { e.stopPropagation(); toggle() }}>⋯</button>
      {open && pos && (
        <div className="entity-menu__pop" style={{ position: 'fixed', top: pos.top, left: pos.left, right: 'auto' }}>
          {items.map((it, i) => (
            <button key={i} className={it.danger ? 'entity-menu__danger' : ''}
                    onClick={() => { setOpen(false); it.onClick() }}>{it.label}</button>
          ))}
        </div>
      )}
    </div>
  )
}
