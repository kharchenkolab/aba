/**
 * Fallback search (ui2 M9) — Cmd/Ctrl-K opens a centered modal over the
 * dimmed workspace. Conversational query over the firehose: entities + chat
 * snippets. Pick an entity to focus it.
 */
import { useEffect, useRef, useState } from 'react'
// Search placeholder text dispatches through the search-facet
// registry. The shell never lists "figures, findings, datasets";
// the active content pack decides what's usefully searchable.
import { search_placeholder } from '../lib/searchFacets'
import './SearchModal.css'

interface EntityHit { id: string; type: string; title: string; status: string; created_at: string }
interface MsgHit { id: number; role: string; ts: string; snippet: string }

interface Props {
  open: boolean
  onClose: () => void
  onPick: (entityId: string) => void
}

export default function SearchModal({ open, onClose, onPick }: Props) {
  const [q, setQ] = useState('')
  const [ents, setEnts] = useState<EntityHit[]>([])
  const [msgs, setMsgs] = useState<MsgHit[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) { setQ(''); setEnts([]); setMsgs([]); setTimeout(() => inputRef.current?.focus(), 30) }
  }, [open])

  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => {
      if (!q.trim()) { setEnts([]); setMsgs([]); return }
      fetch(`/api/search?q=${encodeURIComponent(q)}`)
        .then(r => (r.ok ? r.json() : Promise.reject(r)))
        .then(d => { setEnts(d.entities ?? []); setMsgs(d.messages ?? []) })
        .catch(() => {})
    }, 180)
    return () => clearTimeout(t)
  }, [q, open])

  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) { if (e.key === 'Escape') onClose() }
    if (open) document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  const total = ents.length + msgs.length

  return (
    <div className="search-overlay" onMouseDown={onClose}>
      <div className="search-modal" onMouseDown={e => e.stopPropagation()}>
        <div className="search-input-row">
          <svg className="find" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
          <input
            ref={inputRef}
            className="search-q"
            placeholder={search_placeholder()}
            value={q}
            onChange={e => setQ(e.target.value)}
          />
          <span className="search-kbd">Esc</span>
        </div>

        <div className="search-results">
          {q.trim() && total === 0 && <div className="search-empty">No matches for “{q}”.</div>}

          {ents.length > 0 && (
            <>
              <div className="search-group-label">Artifacts <span className="group-meta">{ents.length}</span></div>
              {ents.map(e => (
                <div key={e.id} className="search-row" onClick={() => { onPick(e.id); onClose() }}>
                  <span className="badge-type">{e.type}</span>
                  <div className="body"><div className="name">{e.title}</div></div>
                  <span className="when">{new Date(e.created_at).toLocaleDateString()}</span>
                </div>
              ))}
            </>
          )}

          {msgs.length > 0 && (
            <>
              <div className="search-group-label">In conversation <span className="group-meta">{msgs.length}</span></div>
              {msgs.map(m => (
                <div key={m.id} className="search-row search-row--msg">
                  <span className="badge-type">{m.role}</span>
                  <div className="body"><div className="snippet">{m.snippet}</div></div>
                </div>
              ))}
            </>
          )}
        </div>

        <div className="search-foot">
          <span>Search across the whole project — nothing is lost.</span>
          <span className="search-kbd">⌘K</span>
        </div>
      </div>
    </div>
  )
}
