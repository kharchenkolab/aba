/**
 * Project-wide search (⌘/Ctrl-K). A centered palette over the dimmed workspace,
 * querying /api/search across entities, FILES (the virtual files tree), and chat.
 * Every result navigates: entities via the claim-aware router, files into the
 * Files tab, chat into its thread. Keyboard: ↑/↓ to move, Enter to open, Esc.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { search_placeholder } from '../lib/searchFacets'
import './SearchModal.css'

interface EntityHit { id: string; type: string; title: string; status: string; created_at: string }
interface FileHit { path: string; name: string; kind: string; entity_id?: string; entity_type?: string; title?: string }
interface MsgHit { id: number; role: string; ts: string; thread_id?: string; snippet: string }

interface Props {
  open: boolean
  onClose: () => void
  onPickEntity: (id: string) => void                     // claim-aware router (goToEntity)
  onPickFile: (path: string) => void                      // open in the Files tab
  onPickMessage: (threadId: string | undefined) => void   // jump to the thread
}

// Wrap the first case-insensitive match of `q` in <mark> for at-a-glance relevance.
function highlight(text: string, q: string) {
  if (!q || !text) return text
  const i = text.toLowerCase().indexOf(q.toLowerCase())
  if (i < 0) return text
  return (<>{text.slice(0, i)}<mark>{text.slice(i, i + q.length)}</mark>{text.slice(i + q.length)}</>)
}

export default function SearchModal({ open, onClose, onPickEntity, onPickFile, onPickMessage }: Props) {
  const [q, setQ] = useState('')
  const [ents, setEnts] = useState<EntityHit[]>([])
  const [files, setFiles] = useState<FileHit[]>([])
  const [msgs, setMsgs] = useState<MsgHit[]>([])
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (open) {
      setQ(''); setEnts([]); setFiles([]); setMsgs([]); setActive(0)
      setTimeout(() => inputRef.current?.focus(), 30)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => {
      if (!q.trim()) { setEnts([]); setFiles([]); setMsgs([]); return }
      fetch(`/api/search?q=${encodeURIComponent(q)}`)
        .then(r => (r.ok ? r.json() : Promise.reject(r)))
        .then(d => { setEnts(d.entities ?? []); setFiles(d.files ?? []); setMsgs(d.messages ?? []); setActive(0) })
        .catch(() => {})
    }, 180)
    return () => clearTimeout(t)
  }, [q, open])

  // Flattened, ordered selectable rows (Files → Artifacts → Chat) for keyboard
  // navigation. Each carries its own activation, so Enter always lands somewhere.
  const rows = useMemo(() => {
    const r: Array<() => void> = []
    files.forEach(f => r.push(() => { onPickFile(f.path); onClose() }))
    ents.forEach(e => r.push(() => { onPickEntity(e.id); onClose() }))
    msgs.forEach(m => r.push(() => { onPickMessage(m.thread_id); onClose() }))
    return r
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files, ents, msgs])

  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === 'Escape') { onClose() }
      else if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, Math.max(rows.length - 1, 0))) }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)) }
      else if (e.key === 'Enter') { e.preventDefault(); rows[active]?.() }
    }
    if (open) document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, rows, active, onClose])

  useEffect(() => {
    const el = listRef.current?.querySelector('.search-row.is-active') as HTMLElement | null
    el?.scrollIntoView({ block: 'nearest' })
  }, [active])

  if (!open) return null
  const total = rows.length
  const entBase = files.length
  const msgBase = files.length + ents.length

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

        <div className="search-results" ref={listRef}>
          {q.trim() && total === 0 && <div className="search-empty">No matches for “{q}”.</div>}

          {files.length > 0 && (
            <>
              <div className="search-group-label">Files <span className="group-meta">{files.length}</span></div>
              {files.map((f, i) => (
                <div key={'f' + f.path}
                     className={'search-row' + (active === i ? ' is-active' : '')}
                     onMouseEnter={() => setActive(i)}
                     onClick={() => { onPickFile(f.path); onClose() }}>
                  <span className="badge-type">{f.entity_type || 'file'}</span>
                  <div className="body">
                    <div className="name">{highlight(f.name, q)}</div>
                    <div className="path-meta">{highlight(f.path, q)}</div>
                  </div>
                </div>
              ))}
            </>
          )}

          {ents.length > 0 && (
            <>
              <div className="search-group-label">Artifacts <span className="group-meta">{ents.length}</span></div>
              {ents.map((e, i) => (
                <div key={'e' + e.id}
                     className={'search-row' + (active === entBase + i ? ' is-active' : '')}
                     onMouseEnter={() => setActive(entBase + i)}
                     onClick={() => { onPickEntity(e.id); onClose() }}>
                  <span className="badge-type">{e.type}</span>
                  <div className="body"><div className="name">{highlight(e.title, q)}</div></div>
                  <span className="when">{new Date(e.created_at).toLocaleDateString()}</span>
                </div>
              ))}
            </>
          )}

          {msgs.length > 0 && (
            <>
              <div className="search-group-label">In conversation <span className="group-meta">{msgs.length}</span></div>
              {msgs.map((m, i) => (
                <div key={'m' + m.id}
                     className={'search-row' + (active === msgBase + i ? ' is-active' : '')}
                     onMouseEnter={() => setActive(msgBase + i)}
                     onClick={() => { onPickMessage(m.thread_id); onClose() }}>
                  <span className="badge-type">{m.role}</span>
                  <div className="body"><div className="snippet">{highlight(m.snippet, q)}</div></div>
                </div>
              ))}
            </>
          )}
        </div>

        <div className="search-foot">
          <span>↑↓ to move · ↵ to open · search across the whole project</span>
          <span className="search-kbd">⌘K</span>
        </div>
      </div>
    </div>
  )
}
