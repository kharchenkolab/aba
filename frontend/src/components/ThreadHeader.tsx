/**
 * Thread brief (ui3 P2). Lives at the top of the right column as a compact
 * summary (question + open-question count + lifecycle), with ⤢ to open the
 * thread full in the center. In `full` mode (the thread focused entity-first)
 * it's the complete management surface: question, all open questions with CRUD,
 * and lifecycle. Empty threads show only a quiet "Frame this investigation".
 */
import { useState } from 'react'
import type { Entity } from '../types'
import './ThreadHeader.css'

interface OQ { id: string; text: string; status: string; source?: string }

interface Props {
  thread: Entity
  onChange: () => void
  onSwitchThread: (id: string) => void
  onOpenFull?: () => void      // ⤢ — focus the thread entity-first
  full?: boolean               // center full-view
}

const LIFECYCLE = ['open', 'parked', 'concluded']

export default function ThreadHeader({ thread, onChange, onSwitchThread, onOpenFull, full }: Props) {
  const meta = (thread.metadata ?? {}) as Record<string, unknown>
  const oqs = (meta.open_questions as OQ[]) ?? []
  const lifecycle = (meta.lifecycle as string) ?? 'open'
  const question = (meta.question as string) ?? ''
  const [editingQ, setEditingQ] = useState(false)
  const [newOQ, setNewOQ] = useState('')
  const openCount = oqs.filter(o => o.status === 'open').length

  const api = `/api/threads/${encodeURIComponent(thread.id)}`
  const patch = async (body: unknown) => {
    await fetch(api, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    onChange()
  }
  const addOQ = async () => {
    const t = newOQ.trim(); if (!t) return
    setNewOQ('')
    await fetch(`${api}/open-questions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: t, source: 'user' }) })
    onChange()
  }
  const patchOQ = async (id: string, body: unknown) => {
    await fetch(`${api}/open-questions/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); onChange()
  }
  const delOQ = async (id: string) => { await fetch(`${api}/open-questions/${id}`, { method: 'DELETE' }); onChange() }
  const promoteOQ = async (id: string) => {
    const r = await fetch(`${api}/open-questions/${id}/promote`, { method: 'POST' })
    if (r.ok) { const d = await r.json(); onChange(); onSwitchThread(d.thread.id) }
  }

  // Empty + compact → a single quiet CTA. (In full mode we always show the form.)
  if (!full && !question && oqs.length === 0) {
    return (
      <div className="brief brief--empty">
        <button className="brief__cta" onClick={onOpenFull}>✦ Frame this investigation thread</button>
      </div>
    )
  }

  const questionBlock = (
    editingQ ? (
      <textarea className="brief__q-input" defaultValue={question} autoFocus rows={full ? 2 : 3}
        placeholder="What is this thread investigating?"
        onBlur={e => { setEditingQ(false); const v = e.target.value.trim(); if (v !== question) patch({ question: v }) }}
        onKeyDown={e => { if (e.key === 'Escape') setEditingQ(false) }} />
    ) : (
      <div className="brief__q" onClick={() => setEditingQ(true)}>
        <span className="brief__q-label">Question</span>
        {question || <span className="brief__q-empty">What is this thread investigating?</span>}
        {meta.question_source === 'guide' && question && <span className="brief__suggested">✦ Guide</span>}
      </div>
    )
  )

  return (
    <div className={`brief ${full ? 'brief--full' : ''}`}>
      <div className="brief__head">
        {questionBlock}
        {!full && (
          <button className="brief__full" title="Open the thread full" onClick={onOpenFull}>⤢</button>
        )}
      </div>

      <div className="brief__lifecycle">
        {LIFECYCLE.map(s => (
          <button key={s} className={`brief__lc ${lifecycle === s ? 'is-on' : ''}`} onClick={() => patch({ lifecycle: s })}>{s}</button>
        ))}
      </div>

      <div className="brief__oqs">
        <div className="brief__oqs-label">Open questions <span className="brief__count">{openCount}</span></div>
        {oqs.map(o => (
          <div key={o.id} className={`brief__oq is-${o.status}`}>
            <span className="brief__oq-text">{o.text}</span>
            <span className="brief__oq-actions">
              {o.status !== 'answered' && o.status !== 'promoted' && <button onClick={() => patchOQ(o.id, { status: 'answered' })} title="Mark answered">✓</button>}
              {o.status === 'open' && <button onClick={() => patchOQ(o.id, { status: 'parked' })} title="Park">❙❙</button>}
              {(o.status === 'parked' || o.status === 'answered') && <button onClick={() => patchOQ(o.id, { status: 'open' })} title="Reopen">↺</button>}
              {o.status !== 'promoted' && <button onClick={() => promoteOQ(o.id)} title="Promote to its own thread">➦</button>}
              <button onClick={() => delOQ(o.id)} title="Remove">×</button>
            </span>
          </div>
        ))}
        <input className="brief__oq-add" value={newOQ} onChange={e => setNewOQ(e.target.value)}
          placeholder="+ add an open question" onKeyDown={e => { if (e.key === 'Enter') addOQ() }} />
      </div>
    </div>
  )
}
