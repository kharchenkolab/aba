/**
 * Result (kept observation) view. A Result is a *grouping*: one editable
 * "reading" (interpretation) plus an ordered list of member panels — figures,
 * tables, values, and text notes. The single-member case (the common one)
 * renders as a near-bare cell. Results grow deliberately: + Add panel pulls a
 * recent figure from this thread; + Note adds inline prose. Members are sections
 * of this one page — never separate destinations.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
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
  const [synthesisOpen, setSynthesisOpen] = useState(false)
  const [picker, setPicker] = useState(false)
  const [zoom, setZoom] = useState<string | null>(null)
  const [focusMember, setFocusMember] = useState<string | null>(null)

  useEffect(() => { setReading(interpretation); setTitle(result.title) }, [result.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-resize the Result-level synthesis textarea (rarely used, but if
  // the user writes one we let it grow with the content like the figure
  // captions do).
  const readingRef = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const el = readingRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = el.scrollHeight + 'px'
  }, [reading])
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
  // On any user edit, flip interpretation_origin to 'user' so the ✨ tag disappears
  // and the background auto-interpret won't overwrite the user's text later.
  const saveReading = () => {
    if (reading !== interpretation)
      api(`/api/entities/${rid}`, 'PATCH', { interpretation: reading, interpretation_origin: 'user' })
  }
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
  // + Upload evidence: send a file straight into THIS Result as a new member.
  // The Result's interpretation is NOT regenerated — the description belongs to
  // the Result as a whole; new evidence rides under it.
  const uploadRef = useRef<HTMLInputElement>(null)
  const uploadEvidence = async (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    await fetch(`/api/results/${rid}/upload-evidence`, { method: 'POST', body: fd }).catch(() => {})
    onChange()
  }
  const removeMember = (mid: string) => api(`/api/results/${rid}/members/${encodeURIComponent(mid)}`, 'DELETE')
  const saveCaption = (mid: string, caption: string, origin: 'user' = 'user') =>
    api(`/api/results/${rid}/members/${encodeURIComponent(mid)}`, 'PATCH', { caption, caption_origin: origin })
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

      {/* Result-level synthesis — OPTIONAL. The figure caption lives on the
          MEMBER (under the image, where it belongs); this textarea is only
          for an explicit cross-evidence synthesis the user writes (e.g.
          "the QC + clustering together suggest…"). Quiet by default —
          collapsed to a small "+ Add synthesis" affordance until used. */}
      {(reading.trim() || synthesisOpen) ? (
        <div className="rv__reading-row">
          <textarea ref={readingRef} className="rv__reading" value={reading}
                    placeholder="Synthesis across panels (optional)…"
                    onChange={e => setReading(e.target.value)} onBlur={saveReading} rows={1} />
        </div>
      ) : (
        <button className="rv__add-synthesis" onClick={() => setSynthesisOpen(true)}>
          + Add a synthesis across panels (optional)
        </button>
      )}

      <div className="rv__add">
        <div className="rv__add-row">
          <button className="rv__add-btn" onClick={() => setPicker(p => !p)}>＋ Add panel</button>
          <button className="rv__add-btn" onClick={addNote}>＋ Note</button>
          <button className="rv__add-btn" onClick={() => uploadRef.current?.click()}>＋ Upload evidence</button>
          <input ref={uploadRef} type="file" style={{ display: 'none' }}
                 accept="image/*,.csv,.tsv,.xlsx,.pdf"
                 onChange={e => { const f = e.target.files?.[0]; if (f) uploadEvidence(f); e.target.value = '' }} />
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
  onCaption: (mid: string, caption: string, origin: 'user') => void
  onText: (mid: string, text: string) => void
  onFocus: (id: string) => void
  onDiscuss?: () => void
}) {
  const [caption, setCaption] = useState(member.caption ?? '')
  const [text, setText] = useState(member.text ?? '')
  useEffect(() => { setCaption(member.caption ?? ''); setText(member.text ?? '') }, [member.id]) // eslint-disable-line react-hooks/exhaustive-deps
  // Pick up server-side updates to the caption (background auto_interpret
  // daemon completes ~1-3s after pin) — but only if the user hasn't started
  // editing locally (i.e. local is still empty OR matches the OLD server
  // value). Prevents the daemon's write from clobbering an in-flight edit.
  useEffect(() => {
    setCaption(prev => (prev === '' || prev === member.caption ? (member.caption ?? '') : prev))
  }, [member.caption])

  // Auto-resize the caption textarea to its content. The figure caption is
  // a free-form paragraph (per the new prompt), not a one-liner — without
  // auto-resize the bottom of a multi-sentence caption gets cut off.
  const capRef = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const el = capRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = el.scrollHeight + 'px'
  }, [caption])

  const captionOrigin = (member as { caption_origin?: 'ai' | 'user' }).caption_origin ?? 'user'

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
      <div className="rv-panel__caption-row">
        <textarea ref={capRef} className="rv-panel__caption" value={caption} placeholder="Add a caption…" rows={1}
                  onChange={e => setCaption(e.target.value)}
                  onBlur={() => { if (caption !== (member.caption ?? '')) onCaption(member.id, caption, 'user') }} />
        {captionOrigin === 'ai' && (
          <span className="rv-panel__ai-tag" title="AI-generated from the figure + producing code + chat context. Edit to claim it as yours.">✨ AI</span>
        )}
      </div>
    </div>
  )
}
