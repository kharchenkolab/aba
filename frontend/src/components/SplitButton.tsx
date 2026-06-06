/**
 * SplitButton — primary action button with a hover-revealed dropdown tab
 * to its left. Used in the FocusCanvas action bar so a figure's "Chat
 * about this" gesture can expand into "Make a revision" / "Reproduce"
 * without taking up extra space at rest.
 *
 * Design (matches the user's brief 2026-06-06):
 *   - Default state: just the primary button
 *   - Hover over the button group: a small chevron tab slides out on the LEFT
 *   - Click the chevron tab: a dropdown menu appears below
 *   - Outside-click / Esc closes the dropdown
 *
 * No UI library is used (matches the rest of the codebase). The dropdown
 * portals to <body> so it doesn't get clipped by overflow ancestors
 * (matches EntityMenu's pattern).
 */
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import './SplitButton.css'


export interface SplitButtonOption {
  label: string
  /** Optional short description shown beneath the label in the dropdown. */
  description?: string
  onClick: () => void
  /** If true, the option is visually emphasized (e.g. the default action). */
  emphasis?: boolean
}


interface Props {
  primary: { label: string; title?: string; onClick: () => void }
  options: SplitButtonOption[]
  /** Extra className for the primary button (e.g. accent variant). */
  className?: string
}


export default function SplitButton({ primary, options, className }: Props) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const chevronRef = useRef<HTMLButtonElement | null>(null)
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null)

  // Outside-click closes the dropdown. We listen on capture so clicks on
  // the chevron itself don't get pre-empted.
  useEffect(() => {
    if (!open) return
    const onDocClick = (ev: MouseEvent) => {
      const target = ev.target as Node
      if (wrapRef.current && wrapRef.current.contains(target)) return
      // Portal contents live outside the wrapper — detect via the
      // [data-split-button-menu] marker on the menu container itself.
      const menu = (target instanceof Element)
        ? target.closest('[data-split-button-menu]') : null
      if (menu) return
      setOpen(false)
    }
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') setOpen(false)
    }
    document.addEventListener('click', onDocClick, true)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('click', onDocClick, true)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Pin the menu to just below the chevron (or to the right edge of the
  // primary button) — recomputed each open so window resizes don't strand
  // a stale position.
  useEffect(() => {
    if (!open || !wrapRef.current) return
    const rect = wrapRef.current.getBoundingClientRect()
    setPos({
      top: rect.bottom + 4,
      left: rect.left,
      width: Math.max(rect.width, 220),
    })
  }, [open])

  return (
    <div
      ref={wrapRef}
      className={`split-button ${open ? 'split-button--open' : ''}`}
    >
      <button
        ref={chevronRef}
        type="button"
        className="split-button__chevron"
        title="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(v => !v)}
      >
        ▾
      </button>
      <button
        type="button"
        className={`split-button__primary ${className ?? ''}`}
        title={primary.title}
        onClick={primary.onClick}
      >
        {primary.label}
      </button>

      {open && pos && createPortal(
        <div
          data-split-button-menu
          className="split-button__menu"
          style={{ top: pos.top, left: pos.left, minWidth: pos.width }}
          role="menu"
        >
          {options.map((opt, i) => (
            <button
              key={i}
              type="button"
              role="menuitem"
              className={`split-button__menu-item ${opt.emphasis ? 'split-button__menu-item--emphasis' : ''}`}
              onClick={() => { opt.onClick(); setOpen(false) }}
            >
              <span className="split-button__menu-label">{opt.label}</span>
              {opt.description && (
                <span className="split-button__menu-desc">{opt.description}</span>
              )}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  )
}
