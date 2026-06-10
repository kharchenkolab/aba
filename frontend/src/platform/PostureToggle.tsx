/**
 * Chat-first / Entity-first posture toggle (ui2). Switching changes which
 * surface is primary; the other becomes the peek. Keyboard: C / F.
 */
import { useEffect } from 'react'
import './PostureToggle.css'

export type Posture = 'chat' | 'entity'

interface Props {
  posture: Posture
  onChange: (p: Posture) => void
  /** Label for the entity side (e.g. "Figure", "Finding", "Dataset"). */
  entityLabel?: string
}

export default function PostureToggle({ posture, onChange, entityLabel = 'Entity' }: Props) {
  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      // Don't grab modified keypresses — Ctrl/Cmd-C is COPY, not switch-to-chat.
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const ae = document.activeElement as HTMLElement | null
      const tag = ae?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || ae?.isContentEditable) return
      if (e.key === 'c' || e.key === 'C') onChange('chat')
      if (e.key === 'f' || e.key === 'F') onChange('entity')
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onChange])

  return (
    <div className="posture-toggle">
      <button className={posture === 'chat' ? 'active' : ''} onClick={() => onChange('chat')}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        Chat<span className="posture-kbd">C</span>
      </button>
      <button className={posture === 'entity' ? 'active' : ''} onClick={() => onChange('entity')}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
        {entityLabel}<span className="posture-kbd">F</span>
      </button>
    </div>
  )
}
