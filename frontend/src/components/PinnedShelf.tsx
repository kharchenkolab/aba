/**
 * Pinned shelf (ui3 P2) — the right peek in chat-first posture. Lists the
 * current thread's kept Results with thumbnail + an editable one-line
 * interpretation, plus per-item actions (unpin, move-to-thread, claim-from
 * [stub → Phase C]). The interpretation is captured here the moment a pin
 * appears, so the "what it means" is recorded when the user knows it.
 */
import { useRef, useState } from 'react'
import type { Entity } from '../types'
import './PinnedShelf.css'

interface Props {
  entities: Entity[]
  threadId: string | null        // resolved current-thread id
  threads: Entity[]              // move-to targets
  onChange: () => void
  onFocus: (id: string) => void
  onClaimFrom: (resultId: string) => void
}

const KEEPABLE = new Set(['figure', 'table', 'note'])

export default function PinnedShelf({ entities, threadId, threads, onChange, onFocus, onClaimFrom }: Props) {
  // Everything kept in this thread — figures, tables, and kept message notes.
  const pins = entities.filter(e =>
    e.pinned && KEEPABLE.has(e.type) && e.status !== 'archived'
    && !!threadId && e.metadata?.thread_id === threadId)
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
        </div>
        <div className="panel-head-actions">
          <span className="panel-head-sub">{pins.length}</span>
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
        {pins.map(p => (
          <PinRow key={p.id} pin={p} threads={threads} onChange={onChange} onFocus={onFocus} onClaimFrom={onClaimFrom} />
        ))}
      </div>
    </div>
  )
}

function PinRow({ pin, threads, onChange, onFocus, onClaimFrom }:
  { pin: Entity; threads: Entity[]; onChange: () => void; onFocus: (id: string) => void; onClaimFrom: (id: string) => void }) {
  const isNote = pin.type === 'note'
  const interp = (pin.metadata?.interpretation as string) ?? ''
  const noteText = (pin.metadata?.text as string) ?? ''
  const [editing, setEditing] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  const patch = async (body: unknown) => {
    await fetch(`/api/entities/${encodeURIComponent(pin.id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    onChange()
  }
  // Unpinning a note toggles its kept state by content key (so the chat cell's
  // pin indicator stays in sync); figures/tables just clear the pinned flag.
  const unpin = async () => {
    if (isNote) {
      await fetch('/api/messages/pin', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: pin.metadata?.source_key, text: noteText }),
      })
      onChange()
    } else {
      patch({ pinned: false })
    }
  }
  const moveTargets = threads.filter(t => t.id !== pin.metadata?.thread_id)

  return (
    <div className={`pin-row ${isNote ? 'pin-row--note' : ''}`}>
      {pin.artifact_path && (
        <img className="pin-row__thumb" src={pin.artifact_path} alt={pin.title} onClick={() => onFocus(pin.id)} />
      )}
      <div className="pin-row__body">
        <div className="pin-row__title" onClick={() => onFocus(pin.id)} title={pin.title}>{pin.title}</div>
        {isNote ? (
          <div className="pin-row__note-text">{noteText}</div>
        ) : editing ? (
          <input className="pin-row__interp-input" defaultValue={interp} autoFocus
            placeholder="one-line interpretation…"
            onBlur={e => { setEditing(false); if (e.target.value !== interp) patch({ interpretation: e.target.value }) }}
            onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditing(false) }} />
        ) : (
          <div className={`pin-row__interp ${interp ? '' : 'is-empty'}`} onClick={() => setEditing(true)}>
            {interp || 'add a one-line interpretation…'}
          </div>
        )}
      </div>
      <div className="pin-row__actions">
        <button className="pin-row__act pin-row__act--claim" title="Make a claim from this"
          onClick={() => onClaimFrom(pin.id)}>claim</button>
        <button className="pin-row__act" title="Move to another thread" onClick={() => setMenuOpen(o => !o)}>⤳</button>
        <button className="pin-row__act" title="Unpin" onClick={unpin}>×</button>
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
