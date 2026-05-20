/**
 * Three-dot context menu for an entity. Opens a small floating popover
 * with: rename, edit notes, edit tags, pin/unpin, download artifact,
 * archive/restore. The menu is the user's main lever for organizing
 * the project.
 */
import { useEffect, useRef, useState } from 'react'
import type { Entity } from '../types'
import './EntityMenu.css'

interface Props {
  entity: Entity
  onChange: () => void
}

type Editing =
  | { kind: 'rename' }
  | { kind: 'notes' }
  | { kind: 'tags' }
  | null

export default function EntityMenu({ entity, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Editing>(null)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
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
        className="entity-menu__btn"
        onClick={e => { e.stopPropagation(); setOpen(v => !v) }}
        title="More actions"
      >
        ⋯
      </button>
      {open && !editing && (
        <div className="entity-menu__pop">
          <button onClick={() => setEditing({ kind: 'rename' })}>Rename…</button>
          <button onClick={() => setEditing({ kind: 'notes' })}>Edit notes…</button>
          <button onClick={() => setEditing({ kind: 'tags' })}>Edit tags…</button>
          <button onClick={() => patch({ pinned: !entity.pinned })}>
            {entity.pinned ? 'Unpin' : 'Pin'}
          </button>
          {canDownload && <button onClick={download}>Download…</button>}
          {isArchived ? (
            <button onClick={restore}>Restore</button>
          ) : (
            <button onClick={del} className="entity-menu__danger">Archive</button>
          )}
        </div>
      )}
      {open && editing?.kind === 'rename' && (
        <EditOne
          label="Rename"
          value={entity.title}
          onCancel={() => setEditing(null)}
          onSubmit={v => patch({ title: v })}
        />
      )}
      {open && editing?.kind === 'notes' && (
        <EditMulti
          label="Notes"
          value={entity.notes ?? ''}
          placeholder="A short note for future-you…"
          onCancel={() => setEditing(null)}
          onSubmit={v => patch({ notes: v })}
        />
      )}
      {open && editing?.kind === 'tags' && (
        <EditOne
          label="Tags (comma-separated)"
          value={entity.tags.join(', ')}
          onCancel={() => setEditing(null)}
          onSubmit={v => patch({
            tags: v.split(',').map(s => s.trim()).filter(Boolean),
          })}
        />
      )}
    </div>
  )
}

function EditOne({
  label, value, onCancel, onSubmit,
}: {
  label: string; value: string;
  onCancel: () => void; onSubmit: (v: string) => void;
}) {
  const [v, setV] = useState(value)
  return (
    <div className="entity-menu__pop entity-menu__edit">
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

function EditMulti({
  label, value, placeholder, onCancel, onSubmit,
}: {
  label: string; value: string; placeholder?: string;
  onCancel: () => void; onSubmit: (v: string) => void;
}) {
  const [v, setV] = useState(value)
  return (
    <div className="entity-menu__pop entity-menu__edit">
      <div className="entity-menu__label">{label}</div>
      <textarea
        className="entity-menu__textarea"
        value={v}
        placeholder={placeholder}
        onChange={e => setV(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Escape') onCancel()
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) onSubmit(v)
        }}
        rows={4}
        autoFocus
      />
      <div className="entity-menu__buttons">
        <button onClick={onCancel}>Cancel</button>
        <button onClick={() => onSubmit(v)} className="entity-menu__primary">Save</button>
      </div>
    </div>
  )
}
