/**
 * Result (kept observation) view. A Result is a *grouping*: one editable
 * "reading" (interpretation) plus an ordered list of member panels — figures,
 * tables, values, and text notes. The single-member case (the common one)
 * renders as a near-bare cell. Results grow deliberately: + Add panel pulls a
 * recent figure from this thread; + Note adds inline prose. Members are sections
 * of this one page — never separate destinations.
 */
import { useEffect, useState } from 'react'
import type { Entity, ResultMember } from '../types'
import { EntityGlyph } from './icons'
import './ResultView.css'

const IMG = /\.(png|jpe?g|svg|webp|gif)$/i

export default function ResultView({ result, entities, onChange, onFocus, onAsk, onChatResult }: {
  result: Entity
  entities: Entity[]
  onChange: () => void
  onFocus: (id: string) => void
  onAsk?: (t: string) => void
  onChatResult?: (label: string, thumb?: string, annotation?: { image: string; note: string }) => void
}) {
  const members = (result.metadata?.members as ResultMember[]) ?? []
  const interpretation = (result.metadata?.interpretation as string) ?? ''
  const threadId = result.metadata?.thread_id as string | undefined
  const cellById = (id?: string) => (id ? entities.find(e => e.id === id) : undefined)

  const [titleEdit, setTitleEdit] = useState(false)
  const [title, setTitle] = useState(result.title)
  const [reading, setReading] = useState(interpretation)
  const [picker, setPicker] = useState(false)
  const [zoom, setZoom] = useState<string | null>(null)
  const [focusMember, setFocusMember] = useState<string | null>(null)

  useEffect(() => { setReading(interpretation); setTitle(result.title) }, [result.id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!zoom) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setZoom(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [zoom])

  async function api(path: string, method = 'POST', body?: unknown) {
    await fetch(path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    }).catch(() => {})
    onChange()
  }
  const rid = encodeURIComponent(result.id)
  const saveTitle = () => { setTitleEdit(false); if (title.trim() && title.trim() !== result.title) api(`/api/entities/${rid}`, 'PATCH', { title: title.trim() }) }
  const saveReading = () => { if (reading !== interpretation) api(`/api/entities/${rid}`, 'PATCH', { interpretation: reading }) }
  const addFigure = (cellId: string) => { setPicker(false); api(`/api/results/${rid}/members`, 'POST', { kind: 'figure', ref: cellId }) }
  const addNote = async () => {
    const r = await fetch(`/api/results/${rid}/members`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'text', text: '' }),
    }).catch(() => null)
    if (r && r.ok) {
      const res = await r.json()
      const ms = (res.metadata?.members as ResultMember[]) ?? []
      setFocusMember(ms[ms.length - 1]?.id ?? null)  // autofocus the new note
    }
    onChange()
  }
  const removeMember = (mid: string) => api(`/api/results/${rid}/members/${encodeURIComponent(mid)}`, 'DELETE')
  const saveCaption = (mid: string, caption: string) => api(`/api/results/${rid}/members/${encodeURIComponent(mid)}`, 'PATCH', { caption })
  const saveText = (mid: string, text: string) => api(`/api/results/${rid}/members/${encodeURIComponent(mid)}`, 'PATCH', { text })
  const move = (idx: number, dir: -1 | 1) => {
    const ids = members.map(m => m.id)
    const j = idx + dir
    if (j < 0 || j >= ids.length) return
    ;[ids[idx], ids[j]] = [ids[j], ids[idx]]
    api(`/api/results/${rid}/reorder`, 'POST', { order: ids })
  }

  const memberRefs = new Set(members.map(m => m.ref).filter(Boolean))
  const candidates = entities.filter(e =>
    e.type === 'figure' && e.artifact_path && IMG.test(e.artifact_path)
    && e.metadata?.thread_id === threadId && !memberRefs.has(e.id) && e.status !== 'archived')

  return (
    <div className="rv">
      {titleEdit ? (
        <input className="rv__title-input" autoFocus value={title}
               onChange={e => setTitle(e.target.value)} onBlur={saveTitle}
               onKeyDown={e => { if (e.key === 'Enter') saveTitle(); if (e.key === 'Escape') { setTitle(result.title); setTitleEdit(false) } }} />
      ) : (
        <h1 className="rv__title" onClick={() => setTitleEdit(true)} title="Click to rename">{result.title}</h1>
      )}

      {/* Reading — the headline interpretation of the observation. */}
      <textarea className="rv__reading" value={reading} placeholder="What does this show? (one-line reading)"
                onChange={e => setReading(e.target.value)} onBlur={saveReading} rows={1} />

      <div className="rv__members">
        {members.map((m, i) => (
          <MemberPanel key={m.id} member={m} idx={i} count={members.length}
            cell={cellById(m.ref)} autoFocus={m.id === focusMember} onZoom={setZoom} onRemove={removeMember} onMove={move}
            onCaption={saveCaption} onText={saveText} onFocus={onFocus}
            onDiscuss={onChatResult || onAsk ? (() => {
              const cell = cellById(m.ref)
              const label = m.kind === 'text' ? 'this note' : (cell?.title ?? m.kind)
              if (onChatResult && cell?.artifact_path) onChatResult(cell.title, cell.artifact_path)
              else onAsk?.(`Let's look at "${label}" in result "${result.title}".`)
            }) : undefined} />
        ))}
        {members.length === 0 && <div className="rv__empty">Empty result — add a panel or a note below.</div>}
      </div>

      <div className="rv__add">
        <div className="rv__add-row">
          <button className="rv__add-btn" onClick={() => setPicker(p => !p)}>＋ Add panel</button>
          <button className="rv__add-btn" onClick={addNote}>＋ Note</button>
        </div>
        {picker && (
          <div className="rv__picker">
            <div className="rv__picker-head">Recent figures in this thread</div>
            {candidates.length === 0 && <div className="rv__picker-empty">No unused figures in this thread. Ask the Guide to make one, or pin one from chat.</div>}
            <div className="rv__picker-grid">
              {candidates.map(c => (
                <button key={c.id} className="rv__picker-cell" title={c.title} onClick={() => addFigure(c.id)}>
                  <img src={c.artifact_path!} alt={c.title} />
                  <span>{c.title}</span>
                </button>
              ))}
            </div>
            {onAsk && (
              <button className="rv__picker-ask" onClick={() => { setPicker(false); onAsk(`Make a panel for the result "${result.title}": `) }}>
                ✦ Ask Guide to make one…
              </button>
            )}
          </div>
        )}
      </div>

      {zoom && (
        <div className="rv__zoom" onClick={() => setZoom(null)}>
          <img src={zoom} onClick={e => e.stopPropagation()} />
          <button className="rv__zoom-x" onClick={() => setZoom(null)}>×</button>
        </div>
      )}
    </div>
  )
}

function MemberPanel({ member, idx, count, cell, autoFocus, onZoom, onRemove, onMove, onCaption, onText, onFocus, onDiscuss }: {
  member: ResultMember
  idx: number
  count: number
  cell?: Entity
  autoFocus?: boolean
  onZoom: (url: string) => void
  onRemove: (mid: string) => void
  onMove: (idx: number, dir: -1 | 1) => void
  onCaption: (mid: string, caption: string) => void
  onText: (mid: string, text: string) => void
  onFocus: (id: string) => void
  onDiscuss?: () => void
}) {
  const [caption, setCaption] = useState(member.caption ?? '')
  const [text, setText] = useState(member.text ?? '')
  useEffect(() => { setCaption(member.caption ?? ''); setText(member.text ?? '') }, [member.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const controls = (
    <span className="rv-panel__ctl">
      {count > 1 && <button title="Move up" disabled={idx === 0} onClick={() => onMove(idx, -1)}>↑</button>}
      {count > 1 && <button title="Move down" disabled={idx === count - 1} onClick={() => onMove(idx, 1)}>↓</button>}
      {onDiscuss && <button title="Discuss with Guide" onClick={onDiscuss}>💬</button>}
      <button title="Remove from result" onClick={() => onRemove(member.id)}>×</button>
    </span>
  )

  if (member.kind === 'text') {
    return (
      <div className="rv-panel rv-panel--text">
        <textarea className="rv-panel__note" value={text} placeholder="Write a note…" autoFocus={autoFocus}
                  onChange={e => setText(e.target.value)} onBlur={() => onText(member.id, text)} />
        {controls}
      </div>
    )
  }

  const url = cell?.artifact_path ?? undefined
  return (
    <div className="rv-panel">
      <div className="rv-panel__cell">
        {member.kind === 'figure' && url
          ? <img className="rv-panel__img" src={url} alt={cell?.title} onClick={() => onZoom(url)} title="Click to enlarge" />
          : member.kind === 'table'
          ? <button className="rv-panel__table" onClick={() => cell && onFocus(cell.id)}><EntityGlyph name="table" size={16} /> {cell?.title ?? 'table'}</button>
          : <div className="rv-panel__missing">{cell?.title ?? `${member.kind} (unavailable)`}</div>}
        {controls}
      </div>
      <input className="rv-panel__caption" value={caption} placeholder="Add a caption…"
             onChange={e => setCaption(e.target.value)} onBlur={() => onCaption(member.id, caption)} />
    </div>
  )
}
