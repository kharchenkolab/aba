/**
 * Detached run-output preview — runs in its OWN browser window (popped from the
 * modal's "detach"). Renders the full-res, highlightable image plus a control
 * row (mode tools · highlight · chat · pin) and posts pin/chat/highlight back to
 * the opener window via postMessage. The payload is handed over through a short
 * localStorage token (so the URL stays clean), and it's a real same-origin URL
 * (no about:blank).
 */
import { useEffect, useMemo, useState } from 'react'
import HighlightableImage from '../bio/HighlightableImage'
import './PreviewWindow.css'

interface Payload {
  kind?: string; label: string; thumb?: string; href?: string; size?: string; runId?: string
}

export default function PreviewWindow() {
  const token = useMemo(() => new URLSearchParams(window.location.hash.slice(1)).get('preview') || '', [])
  const payload = useMemo<Payload | null>(() => {
    try {
      const raw = localStorage.getItem(`aba-preview-${token}`)
      return raw ? (JSON.parse(raw) as Payload) : null
    } catch { return null }
  }, [token])

  const [marking, setMarking] = useState(false)
  const [mode, setMode] = useState<'highlight' | 'box'>('highlight')
  const [clearSig, setClearSig] = useState(0)
  const [sent, setSent] = useState('')

  useEffect(() => { if (payload) document.title = payload.label }, [payload])
  // Clean up our token when the window closes.
  useEffect(() => {
    const drop = () => { try { localStorage.removeItem(`aba-preview-${token}`) } catch { /* ignore */ } }
    window.addEventListener('beforeunload', drop)
    return () => window.removeEventListener('beforeunload', drop)
  }, [token])

  if (!payload) return <div className="pw pw--empty">No preview to show (it may have expired — re-open from the run).</div>

  const post = (msg: Record<string, unknown>, flash: string) => {
    try {
      if (window.opener && !window.opener.closed) window.opener.postMessage({ __abaPreview: true, ...msg }, window.location.origin)
    } catch { /* opener gone */ }
    setSent(flash)
    window.setTimeout(() => setSent(''), 2200)
  }
  const pin = () => post({ type: 'pin', runId: payload.runId, item: payload }, 'Pinned to the thread ✓')
  const chat = () => post({ type: 'chat', item: payload }, 'Added to the Guide chat — switch over to send')
  const highlight = (annotation: { image: string; note: string }) =>
    post({ type: 'chat-annot', item: payload, annotation }, 'Highlight added to the Guide chat — switch over to send')

  const pickMode = (m: 'highlight' | 'box') => { setMode(m); setClearSig(s => s + 1) }

  return (
    <div className="pw">
      <div className="pw__bar">
        <span className="pw__title" title={payload.label}>{payload.label}</span>

        {marking && (
          <span className="pw__modes">
            <button className={`pw__btn pw__btn--sm ${mode === 'highlight' ? 'is-sel' : ''}`} title="Freehand marker" onClick={() => pickMode('highlight')}>
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M4 18 Q9 6 14 13 T20 8"/></svg>
            </button>
            <button className={`pw__btn pw__btn--sm ${mode === 'box' ? 'is-sel' : ''}`} title="Box" onClick={() => pickMode('box')}>
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2.4"><rect x="4" y="6" width="16" height="12" rx="1.5"/></svg>
            </button>
            <button className="pw__btn pw__btn--sm" title="Clear mark" onClick={() => setClearSig(s => s + 1)}>
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
            </button>
            <span className="pw__sep" />
          </span>
        )}

        {payload.thumb && (
          <button className={`pw__btn pw__btn--hl ${marking ? 'is-on' : ''}`} title="Highlight a region" onClick={() => setMarking(m => !m)}>
            <svg viewBox="0 0 24 24" width="15" height="15" fill="#fde047" stroke="#ca8a04" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M15.6 2.6a2 2 0 012.8 0l3 3a2 2 0 010 2.8l-9 9-5.2 1.2 1.2-5.2 9-9zM5 19h14v2H5z"/></svg>
          </button>
        )}
        <button className="pw__btn" title="Discuss with Guide" onClick={chat}>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        </button>
        <button className="pw__btn" title="Pin as a result (evidence)" onClick={pin}>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 17v5M9 3h6l-1 7 3 3H7l3-3z"/></svg>
        </button>
      </div>

      <div className="pw__body">
        {payload.thumb
          ? <HighlightableImage src={payload.thumb} label={payload.label} onAttach={highlight} className="pw__img"
                                marking={marking} onMarkingChange={setMarking} mode={mode} onModeChange={setMode}
                                clearMarkSignal={clearSig} hideToolbar showToggle={false} />
          : <div className="pw__noimg">No image to preview.</div>}
      </div>

      {sent && <div className="pw__toast">{sent}</div>}
    </div>
  )
}
