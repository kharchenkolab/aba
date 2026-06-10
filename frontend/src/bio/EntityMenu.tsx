/**
 * Three-dot context menu for an entity. Opens a small floating popover
 * with: rename, edit notes, edit tags, pin/unpin, download artifact,
 * archive/restore. The menu is the user's main lever for organizing
 * the project.
 */
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Entity } from '../types'
import { typeHasChatGesture, typeOf } from '../entityTypes'
import { entity_menu_traits } from '../lib/menuActions'
import './EntityMenu.css'

interface Props {
  entity: Entity
  onChange: () => void
}

// Pin = "promote this evidence into a Result" (lifecycle/promote.pin_evidence).
// Pinnability is declared per type in `creation.user_gestures_chat` of
// the entity-type YAMLs (Phase 4.6) — figure/table/note/narrative include
// "pin"; result/claim/finding don't (they ARE the curation layer, not
// pinnable themselves). The fallback (bio menuActions registry) covers
// the brief window before the catalog has loaded.
function isPinnable(entityType: string): boolean {
  // If the catalog isn't loaded yet, typeOf returns null — fall back
  // to the bio menu-traits registry so the UI doesn't briefly hide the
  // affordance.
  return typeOf(entityType)
    ? typeHasChatGesture(entityType, 'pin')
    : entity_menu_traits(entityType).pinnable
}

type Editing =
  | { kind: 'rename' }
  | { kind: 'tags' }
  | { kind: 'delete' }
  | null

interface Blocker { id: string; type?: string; title?: string; rel_type?: string }

export default function EntityMenu({ entity, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Editing>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const [delError, setDelError] = useState<{ msg: string; refs?: Blocker[] } | null>(null)
  const [deleting, setDeleting] = useState(false)
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

  async function hardDelete() {
    setDeleting(true); setDelError(null)
    // For Results (and any bio type whose menu-traits set cascadeMembers)
    // we cascade to members + their revision chains; the backend skips
    // members that are also referenced from outside the cascade (shared
    // with another Result, cited by a Claim) and detaches only the
    // includes/supports/wasDerivedFrom edges from THIS Result.
    const qs = entity_menu_traits(entity.type).cascadeMembers
      ? '?hard=true&cascade=members'
      : '?hard=true'
    try {
      const r = await fetch(`/api/entities/${encodeURIComponent(entity.id)}${qs}`,
        { method: 'DELETE' })
      if (r.ok) {
        setOpen(false); setEditing(null); onChange()
        return
      }
      // 409 (live refs) returns detail = {error, references}
      let msg = `HTTP ${r.status}`
      let refs: Blocker[] | undefined
      try {
        const j = await r.json()
        if (j?.detail && typeof j.detail === 'object') {
          msg = j.detail.error || msg
          refs = j.detail.references as Blocker[] | undefined
        } else if (typeof j?.detail === 'string') {
          msg = j.detail
        }
      } catch { /* keep default */ }
      setDelError({ msg, refs })
    } finally {
      setDeleting(false)
    }
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
              {isPinnable(entity.type) && (
                <button onClick={pinToResult}>Pin</button>
              )}
              {canDownload && <button onClick={download}>Download…</button>}
              {!isArchived && (
                <button onClick={() => { setDelError(null); setEditing({ kind: 'delete' }) }}
                        className="entity-menu__danger">Delete…</button>
              )}
              {isArchived ? (
                <button onClick={restore}>Restore</button>
              ) : (
                <button onClick={del} className="entity-menu__danger">Archive</button>
              )}
            </div>
          )}
          {editing?.kind === 'delete' && (
            <DeleteConfirm
              entity={entity}
              style={popStyle}
              busy={deleting}
              error={delError}
              onCancel={() => { setEditing(null); setDelError(null) }}
              onConfirm={hardDelete}
            />
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

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function DeleteConfirm({
  entity, onCancel, onConfirm, busy, error, style,
}: {
  entity: Entity
  onCancel: () => void
  onConfirm: () => void
  busy: boolean
  error: { msg: string; refs?: Blocker[] } | null
  style?: React.CSSProperties
}) {
  // Bio menu-traits decides the delete-confirm body variant. dataset →
  // shows file-count + size summary; result → shows member-cascade
  // summary; generic → plain "permanently removed" warning.
  const traits = entity_menu_traits(entity.type)
  const isDataset = traits.deleteVariant === 'dataset'
  const isResult = traits.deleteVariant === 'result'
  const fc = entity.metadata?.file_count as number | undefined
  const bytes = entity.metadata?.size_bytes as number | undefined
  // Count Result members so the user sees what cascade-delete will sweep
  // up before they confirm. Just the immediate members here — revision-
  // chain expansion happens server-side and is summarized after delete
  // via the response's `cascade_deleted` / `skipped` lists.
  const members = (entity.metadata?.members as Array<{ kind?: string; ref?: string }> | undefined) ?? []
  const memberCount = members.length
  return (
    <div className="entity-menu__pop entity-menu__edit" style={style}>
      <div className="entity-menu__label entity-menu__danger">Delete this {entity.type}?</div>
      <div className="entity-menu__delete-body">
        <div><strong>{entity.title}</strong></div>
        {isDataset && typeof fc === 'number' && (
          <div className="entity-menu__delete-meta">
            {fc === 0
              ? <span>empty dataset — folder will be removed.</span>
              : <span>{fc} {fc === 1 ? 'file' : 'files'}{typeof bytes === 'number' ? ` · ${formatBytes(bytes)}` : ''}<br />
                The dataset folder and its contents will be permanently removed.</span>}
          </div>
        )}
        {isResult && (
          <div className="entity-menu__delete-meta">
            {memberCount === 0
              ? <span>The Result and its synthesis text will be permanently removed.</span>
              : <span>
                  The Result will be removed along with its <strong>{memberCount}</strong>{' '}
                  member{memberCount === 1 ? '' : 's'} (and their revision chains).
                  Members that are <em>also</em> referenced by another Result or Claim are
                  kept; the Result's edge to them is detached.
                </span>}
          </div>
        )}
        {!isDataset && !isResult && (
          <div className="entity-menu__delete-meta">This entity will be permanently removed (use Archive for a reversible alternative).</div>
        )}
        {error && (
          <div className="entity-menu__delete-error">
            <div>{error.msg}</div>
            {error.refs && error.refs.length > 0 && (
              <ul>
                {error.refs.map((b, i) => (
                  <li key={i}>{b.title || b.id} <em>({b.type || 'entity'}, {b.rel_type})</em></li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
      <div className="entity-menu__buttons">
        <button onClick={onCancel} disabled={busy}>Cancel</button>
        <button onClick={onConfirm} disabled={busy}
                className="entity-menu__primary entity-menu__danger-btn">
          {busy ? 'Deleting…' : 'Delete'}
        </button>
      </div>
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

