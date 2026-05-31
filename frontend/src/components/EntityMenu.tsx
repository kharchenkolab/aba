/**
 * Three-dot context menu for an entity. Opens a small floating popover
 * with: rename, edit notes, edit tags, pin/unpin, download artifact,
 * archive/restore. The menu is the user's main lever for organizing
 * the project.
 */
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Entity } from '../types'
import './EntityMenu.css'

interface Props {
  entity: Entity
  onChange: () => void
}

// Pin = "promote this evidence into a Result" (lifecycle/promote.pin_evidence).
// Result / Claim / Finding ARE the curation layer — they are not pinnable themselves.
const PINNABLE = new Set(['figure', 'table', 'cell', 'note', 'narrative'])

type Editing =
  | { kind: 'rename' }
  | { kind: 'tags' }
  | null

export default function EntityMenu({ entity, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Editing>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)

  // Position the popover with fixed coords from the trigger so it floats free
  // of any overflow-hidden ancestor (e.g. the rounded tree section cards).
  function toggle() {
    if (!open && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect()
      setPos({ top: r.bottom + 4, left: Math.max(8, r.right - 200) })
    }
    setOpen(v => !v); setEditing(null)
  }
  const popStyle = pos
    ? { position: 'fixed' as const, top: pos.top, left: pos.left, right: 'auto' as const }
    : undefined

  useEffect(() => {
    function onClick(e: MouseEvent) {
      const t = e.target as Node
      // The popover is portaled to <body>, so it's NOT inside ref — check both
      // the trigger and the portaled popover, else clicking a menu item self-closes.
      const inTrigger = ref.current?.contains(t)
      const inPop = popRef.current?.contains(t)
      if (!inTrigger && !inPop) setOpen(false)
    }
    if (open) {
      document.addEventListener('mousedown', onClick)
      return () => document.removeEventListener('mousedown', onClick)
    }
  }, [open])

  async function patch(body: Record<string, unknown>) {
    const r = await fetch(`/api/entities/${encodeURIComponent(entity.id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) console.error('patch failed', await r.text())
    setOpen(false)
    setEditing(null)
    onChange()
  }

  async function del() {
    const r = await fetch(`/api/entities/${encodeURIComponent(entity.id)}`, { method: 'DELETE' })
    if (!r.ok) console.error('delete failed', await r.text())
    setOpen(false)
    onChange()
  }

  async function pinToResult() {
    const r = await fetch(`/api/entities/${encodeURIComponent(entity.id)}/pin`, { method: 'POST' })
    if (!r.ok) console.error('pin failed', await r.text())
    setOpen(false)
    onChange()
  }

  async function restore() {
    const r = await fetch(`/api/entities/${encodeURIComponent(entity.id)}/restore`, { method: 'POST' })
    if (!r.ok) console.error('restore failed', await r.text())
    setOpen(false)
    onChange()
  }

  function download() {
    window.open(`/api/entities/${encodeURIComponent(entity.id)}/download`, '_blank')
    setOpen(false)
  }

  const isArchived = entity.status === 'archived'
  const canDownload = !!entity.artifact_path
  if (entity.id === 'workspace') return null

  return (
    <div className="entity-menu" ref={ref} onClick={e => e.stopPropagation()}>
      <button
        ref={btnRef}
        className="entity-menu__btn"
        onClick={e => { e.stopPropagation(); toggle() }}
        title="More actions"
      >
        ⋯
      </button>
      {open && createPortal(
        // Portaled to <body> so the popover escapes any transformed/overflow-clipped
        // ancestor (e.g. the slide-in rail) — position:fixed alone doesn't escape a
        // transformed ancestor. popRef wraps it for the outside-click check.
        <div ref={popRef} className="entity-menu__portal">
          {!editing && (
            <div className="entity-menu__pop" style={popStyle} onClick={e => e.stopPropagation()}>
              <button onClick={() => setEditing({ kind: 'rename' })}>Rename…</button>
              <button onClick={() => setEditing({ kind: 'tags' })}>Edit tags…</button>
              {PINNABLE.has(entity.type) && (
                <button onClick={pinToResult}>Pin</button>
              )}
              {canDownload && <button onClick={download}>Download…</button>}
              {isArchived ? (
                <button onClick={restore}>Restore</button>
              ) : (
                <button onClick={del} className="entity-menu__danger">Archive</button>
              )}
            </div>
          )}
          {editing?.kind === 'rename' && (
            <EditOne
              label="Rename"
              value={entity.title}
              style={popStyle}
              onCancel={() => setEditing(null)}
              onSubmit={v => patch({ title: v })}
            />
          )}
          {editing?.kind === 'tags' && (
            <EditOne
              label="Tags (comma-separated)"
              value={entity.tags.join(', ')}
              style={popStyle}
              onCancel={() => setEditing(null)}
              onSubmit={v => patch({
                tags: v.split(',').map(s => s.trim()).filter(Boolean),
              })}
            />
          )}
        </div>,
        document.body
      )}
    </div>
  )
}

function EditOne({
  label, value, onCancel, onSubmit, style,
}: {
  label: string; value: string;
  onCancel: () => void; onSubmit: (v: string) => void;
  style?: React.CSSProperties;
}) {
  const [v, setV] = useState(value)
  return (
    <div className="entity-menu__pop entity-menu__edit" style={style}>
      <div className="entity-menu__label">{label}</div>
      <input
        className="entity-menu__input"
        value={v}
        onChange={e => setV(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter') onSubmit(v)
          if (e.key === 'Escape') onCancel()
        }}
        autoFocus
      />
      <div className="entity-menu__buttons">
        <button onClick={onCancel}>Cancel</button>
        <button onClick={() => onSubmit(v)} className="entity-menu__primary">Save</button>
      </div>
    </div>
  )
}

