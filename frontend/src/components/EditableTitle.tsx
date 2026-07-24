import { useState, useEffect } from 'react'
import type { ElementType } from 'react'
import { AgentGlyph } from './icons'
import './EditableTitle.css'

/**
 * The ONE inline click-to-rename title, shared by the global header, the
 * generic entity card header, and the Result/Run views. Before this, five
 * surfaces open-coded the swap-to-input pattern with four different hover
 * cues; this unifies the affordance (subtle background fill on hover) and the
 * "AI-suggested" glyph (shown wherever a title's origin is 'ai').
 *
 * Rest state is a click-to-edit `as` element (span/h1/h2 so the host's
 * typography carries through); on click it swaps to an input that inherits the
 * same font, commits on Enter/blur, reverts on Escape.
 */
export default function EditableTitle({
  value, onCommit, aiSuggested, as = 'span',
  className = '', inputClassName = '',
  placeholder = 'Untitled', title = 'Click to rename', ariaLabel,
}: {
  /** Current title text. */
  value: string
  /** Persist a new title (already trimmed + confirmed changed). */
  onCommit: (next: string) => void
  /** Render the AI-suggested glyph after the title (entity title_origin === 'ai'). */
  aiSuggested?: boolean
  /** Rest-state tag — 'span' in the header, 'h1'/'h2' in a card. */
  as?: ElementType
  className?: string
  inputClassName?: string
  placeholder?: string
  title?: string
  ariaLabel?: string
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  // Keep the draft synced with external changes while NOT editing (an agent
  // rename lands, or the focused entity swaps under the same instance).
  useEffect(() => { if (!editing) setDraft(value) }, [value, editing])

  function commit() {
    const t = draft.trim()
    setEditing(false)
    if (!t || t === value) return
    onCommit(t)
  }

  if (editing) {
    return (
      <input className={`edit-title__input ${inputClassName}`} autoFocus value={draft}
             aria-label={ariaLabel ?? 'Edit title'}
             onFocus={e => e.currentTarget.select()}
             onChange={e => setDraft(e.target.value)}
             onBlur={commit}
             onKeyDown={e => {
               if (e.key === 'Enter') commit()
               if (e.key === 'Escape') { setDraft(value); setEditing(false) }
             }} />
    )
  }

  const Tag = as
  return (
    <Tag className={`edit-title ${className}`}
         onClick={() => { setDraft(value); setEditing(true) }}
         title={title}>
      {value || <span className="edit-title__placeholder">{placeholder}</span>}
      {aiSuggested && (
        <span className="edit-title__ai" title="Title suggested by Guide — edit to make it yours">
          <AgentGlyph agent="guide" size={13} />
        </span>
      )}
    </Tag>
  )
}
