/**
 * Pinned shelf (ui3 P2) — the right peek in chat-first posture. Lists the
 * current thread's kept Results with thumbnail + an editable one-line
 * interpretation, plus per-item actions (unpin, move-to-thread, claim-from
 * [stub → Phase C]). The interpretation is captured here the moment a pin
 * appears, so the "what it means" is recorded when the user knows it.
 */
import { useRef, useState } from 'react'
import type { Entity } from '../types'
import { EntityGlyph } from '../components/icons'
import './PinnedShelf.css'

interface Props {
  entities: Entity[]
  threadId: string | null        // resolved current-thread id
  threads: Entity[]              // move-to targets
  onChange: () => void
  onFocus: (id: string) => void
  onClaimFrom: (resultId: string) => void
}

// Shelf now renders Results only — every pin gesture creates (or appends to) a Result.
// Legacy `entity.pinned` flag is no longer consulted.
const KEEPABLE = new Set(['result'])
const IMG = /\.(png|jpe?g|svg|webp|gif)$/i

interface Member { kind: string; ref?: string }

export default function PinnedShelf({ entities, threadId, threads, onChange, onFocus, onClaimFrom }: Props) {
  // Everything kept in this thread — figures, tables, kept notes, and Results.
  const pins = entities.filter(e =>
    KEEPABLE.has(e.type) && e.status !== 'archived'
    && !!threadId && e.metadata?.thread_id === threadId)
  const byId = new Map(entities.map(e => [e.id, e]))
  // A Result's cover thumb + panel count come from its members.
  const coverOf = (e: Entity): { cover?: string; panels?: number } => {
    if (e.type !== 'result') return {}
    const members = (e.metadata?.members as Member[]) ?? []
    const figRef = members.find(m => m.kind === 'figure')?.ref
    const cell = figRef ? byId.get(figRef) : undefined
    const cover = cell?.artifact_path && IMG.test(cell.artifact_path) ? cell.artifact_path : undefined
    return { cover, panels: members.length }
  }
  const fileRef = useRef<HTMLInputElement>(null)

  async function uploadExternal(f: File) {
    const form = new FormData()
    form.append('file', f)
    form.append('thread_id', threadId ?? 'default')
    await fetch('/api/results/external', { method: 'POST', body: form })
    onChange()
  }

  return (
    <div className="surface-panel pinned-shelf">
      <div className="panel-head">
        <div className="panel-head-title">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M12 17v5M9 3h6l-1 7 3 3H7l3-3z" /></svg>
          Pinned in this thread
          {pins.length > 0 && <span className="panel-head-sub">{pins.length}</span>}
        </div>
        <div className="panel-head-actions">
          <input ref={fileRef} type="file" style={{ display: 'none' }}
            onChange={e => { const f = e.target.files?.[0]; if (f) uploadExternal(f); e.target.value = '' }} />
          <button className="pinned-shelf__upload" title="Add an external result (gel, wet-lab, other tool)"
            onClick={() => fileRef.current?.click()}>＋</button>
        </div>
      </div>
      <div className="pinned-shelf__list">
        {pins.length === 0 && (
          <div className="pinned-shelf__empty">
            Pin a figure or table from the chat to keep it here — these become the
            evidence you build claims from.
          </div>
        )}
        {pins.map(p => {
          const { cover, panels } = coverOf(p)
          return <PinRow key={p.id} pin={p} cover={cover} panels={panels} threads={threads} onChange={onChange} onFocus={onFocus} onClaimFrom={onClaimFrom} />
        })}
      </div>
    </div>
  )
}

function PinRow({ pin, cover, panels, threads, onChange, onFocus, onClaimFrom }:
  { pin: Entity; cover?: string; panels?: number; threads: Entity[]; onChange: () => void; onFocus: (id: string) => void; onClaimFrom: (id: string) => void }) {
  const isNote = pin.type === 'note'
  // The figure caption lives on the first figure member (set by the
  // auto_interpret daemon); fall back to the Result-level synthesis
  // (interpretation) only when the user has written one. Old Results
  // before the ontology change may still have interpretation set
  // without a member-caption — same fallback covers that case.
  const members = (pin.metadata?.members as Array<{kind: string, caption?: string}> | undefined) ?? []
  const figureCaption = members.find(m => m.kind === 'figure' && m.caption)?.caption ?? ''
  const resultSynthesis = (pin.metadata?.interpretation as string) ?? ''
  const interp = figureCaption || resultSynthesis
  const noteText = (pin.metadata?.text as string) ?? ''
  const [editing, setEditing] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  const patch = async (body: unknown) => {
    await fetch(`/api/entities/${encodeURIComponent(pin.id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    onChange()
  }
  // Shelf shows Results — unpinning archives the Result (it falls off the shelf
  // by virtue of status='archived'). The unpin logic for an EVIDENCE entity
  // (figure/table/note inside a Result) lives at /api/entities/{id}/unpin and
  // is called when the user acts on the evidence directly; the shelf works on
  // the Result and just archives it.
  const unpin = async () => {
    await fetch(`/api/entities/${encodeURIComponent(pin.id)}`, { method: 'DELETE' }).catch(() => {})
    onChange()
  }
  const moveTargets = threads.filter(t => t.id !== pin.metadata?.thread_id)

  return (
    <div className={`pin-row ${isNote ? 'pin-row--note' : ''}`}>
      {(cover ?? pin.artifact_path) && (
        <img className="pin-row__thumb" src={cover ?? pin.artifact_path!} alt={pin.title} onClick={() => onFocus(pin.id)} />
      )}
      <div className="pin-row__body">
        <div className="pin-row__title" onClick={() => onFocus(pin.id)} title={pin.title}>
          {pin.title}{panels && panels > 1 ? <span className="pin-row__panels"> · {panels} panels</span> : null}
        </div>
        {isNote ? (
          <div className="pin-row__note-text">{noteText}</div>
        ) : editing ? (
          <input className="pin-row__interp-input" defaultValue={interp} autoFocus
            placeholder="one-line caption…"
            onBlur={async e => {
              setEditing(false)
              const v = e.target.value
              if (v === interp) return
              // Write to the figure-member's caption (where the caption
              // lives now). Fall back to Result.interpretation when there
              // is no figure member (pure-text Result, multi-evidence, …).
              const figMember = members.find(m => m.kind === 'figure')
              if (figMember) {
                const mid = (figMember as { id?: string }).id
                await fetch(`/api/results/${encodeURIComponent(pin.id)}/members/${encodeURIComponent(mid ?? '')}`, {
                  method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ caption: v, caption_origin: 'user' }),
                }).catch(() => {})
                onChange()
              } else {
                patch({ interpretation: v })
              }
            }}
            onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditing(false) }} />
        ) : (
          <div className={`pin-row__interp ${interp ? '' : 'is-empty'}`} onClick={() => setEditing(true)}>
            {interp || 'add a one-line caption…'}
          </div>
        )}
      </div>
      <div className="pin-row__actions">
        <button className="pin-row__act pin-row__act--claim" title="Make a claim from this"
          onClick={() => onClaimFrom(pin.id)} aria-label="Make a claim from this">
          <EntityGlyph name="claim" size={14} />
        </button>
        <button className="pin-row__act" title="Move to another thread"
          onClick={() => setMenuOpen(o => !o)} aria-label="Move to another thread">
          {/* Folder-arrow: an outlined folder with a → on top, reads as
              "move to a different bucket". Stronger than the prior ⤳. */}
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none"
               stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
            <path d="M9 13h7M13 10l3 3-3 3" />
          </svg>
        </button>
        <button className="pin-row__act" title="Unpin" onClick={unpin} aria-label="Unpin">
          {/* 14px SVG × so the unpin button matches the other two icons
              in visual weight — the unicode × character rendered ~9-10px
              tall, noticeably smaller than the 14px claim flag + folder. */}
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none"
               stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
        {menuOpen && (
          <div className="pin-row__menu" onMouseLeave={() => setMenuOpen(false)}>
            {moveTargets.length === 0 && <div className="pin-row__menu-empty">no other threads</div>}
            {moveTargets.map(t => (
              <button key={t.id} onClick={() => { patch({ thread_id: t.id }); setMenuOpen(false) }}>{t.title}</button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
