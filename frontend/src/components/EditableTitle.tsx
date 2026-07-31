import { useState, useRef, useLayoutEffect } from 'react'
import type { ElementType, CSSProperties } from 'react'
import { AgentGlyph } from './icons'
import './EditableTitle.css'

/**
 * The ONE inline click-to-rename control, shared by the global header, the
 * generic entity card header, the Result/Run titles, and the Claim statement.
 * Before this, five surfaces open-coded the swap-to-input pattern with four
 * different hover cues and inputs that jumped (short width, cursor-at-end,
 * wrong font).
 *
 * Edit-in-place is the whole point: on click we snapshot the rest element's
 * computed font + measured box and stamp them onto the field, and drop the
 * caret at the START — so the text does not jump, resize, or restyle when you
 * begin editing (a long title just scrolls when you move to the end).
 *
 * `multiline` swaps the field for a textarea (Claim statements, notes): Enter
 * commits, Shift+Enter inserts a newline.
 */
export default function EditableTitle({
  value, onCommit, aiSuggested, as = 'span', multiline = false,
  className = '', placeholder = 'Untitled', title = 'Click to rename', ariaLabel,
}: {
  /** Current text. */
  value: string
  /** Persist a new value (already trimmed + confirmed changed). */
  onCommit: (next: string) => void
  /** Render the AI-suggested glyph after the text (title_origin === 'ai'). */
  aiSuggested?: boolean
  /** Rest-state tag — 'span' in the header, 'h1'/'h2' in a card, 'p' for a statement. */
  as?: ElementType
  /** Multi-line editing (textarea) — for statements / notes. */
  multiline?: boolean
  /** Applied to the REST element so the host's typography carries through; the
   *  field copies that typography via computed style, so it never restyles. */
  className?: string
  placeholder?: string
  title?: string
  ariaLabel?: string
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const restRef = useRef<HTMLElement | null>(null)
  const fieldRef = useRef<HTMLInputElement & HTMLTextAreaElement | null>(null)
  const [fit, setFit] = useState<CSSProperties>({})

  function begin() {
    const el = restRef.current
    if (el) {
      const cs = getComputedStyle(el)
      const rect = el.getBoundingClientRect()
      // Copy the exact text typography so the field renders identical glyphs —
      // no font/size/weight jump — and pin the box so the extent doesn't shift.
      setFit({
        fontFamily: cs.fontFamily, fontSize: cs.fontSize, fontWeight: cs.fontWeight,
        fontStyle: cs.fontStyle, lineHeight: cs.lineHeight, letterSpacing: cs.letterSpacing,
        color: cs.color, textAlign: cs.textAlign as CSSProperties['textAlign'],
        ...(multiline
          ? { width: '100%', height: Math.ceil(rect.height) + 'px' }
          : { width: Math.ceil(rect.width) + 'px' }),
      })
    }
    setDraft(value)
    setEditing(true)
  }

  // Focus + caret-at-START before paint, so entry never scrolls the text to the
  // end or flashes a selection.
  useLayoutEffect(() => {
    if (!editing) return
    const f = fieldRef.current
    if (!f) return
    f.focus()
    try { f.setSelectionRange(0, 0) } catch { /* not all field types allow it */ }
    f.scrollLeft = 0
    f.scrollTop = 0
  }, [editing])

  function commit() {
    const t = draft.trim()
    setEditing(false)
    if (!t || t === value) return
    onCommit(t)
  }
  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && (!multiline || !e.shiftKey)) { e.preventDefault(); commit() }
    if (e.key === 'Escape') { setDraft(value); setEditing(false) }
  }

  if (editing) {
    const common = {
      ref: fieldRef as React.Ref<never>,
      className: `edit-title__input ${multiline ? 'edit-title__input--multiline' : ''}`,
      style: fit,
      value: draft,
      'aria-label': ariaLabel ?? 'Edit',
      onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setDraft(e.target.value),
      onBlur: commit,
      onKeyDown,
    }
    return multiline ? <textarea {...common} /> : <input {...common} />
  }

  const Tag = as
  return (
    <Tag ref={restRef} className={`edit-title ${className}`} onClick={begin} title={title}>
      {value || <span className="edit-title__placeholder">{placeholder}</span>}
      {aiSuggested && (
        <span className="edit-title__ai" title="Title suggested by Guide — edit to make it yours">
          <AgentGlyph agent="guide" size={13} />
        </span>
      )}
    </Tag>
  )
}
