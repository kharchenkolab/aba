import { useState, useRef, useEffect, useLayoutEffect } from 'react'
import type { KeyboardEvent } from 'react'
import './Composer.css'

// Auto-grow cap as a fraction of viewport height — a 4k paste should
// surface, not vanish into a 160px scrollbox. Min keeps it sensible
// when the window is shorter than expected.
const MAX_GROW_PX = () => Math.max(180, Math.floor(window.innerHeight * 0.45))

interface Props {
  onSend: (text: string) => void
  disabled: boolean
  prefill?: string
  onPrefillConsumed?: () => void
  /** Increment to focus the composer (e.g. after a highlight is attached). */
  focusSignal?: number
  /** True iff the agent is currently mid-turn. Changes placeholder copy
   *  and enables the Cmd+Enter Steer shortcut. */
  streaming?: boolean
  /** Cmd/Ctrl+Enter while streaming = "Steer": cancel + send. */
  onSteer?: (text: string) => void
  /** Stop button rendered inside the composer box. Visible only while
   *  streaming. Icon-only, sits next to the Send arrow. */
  onStop?: () => void
}

export default function Composer({ onSend, disabled, prefill, onPrefillConsumed, focusSignal, streaming, onSteer, onStop }: Props) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // When an advisor's "Try it" prefills the composer, drop the text in and
  // focus — the user can edit or just hit Enter.
  useEffect(() => {
    if (prefill) {
      setValue(prefill)
      textareaRef.current?.focus()
      onPrefillConsumed?.()
    }
  }, [prefill, onPrefillConsumed])

  // Focus the composer when signaled (e.g. a region was just highlighted).
  useEffect(() => {
    if (focusSignal) textareaRef.current?.focus()
  }, [focusSignal])

  // Type-anywhere → chat. If the user starts typing a printable character
  // and they're not in some other input (project-name field, dialog, etc.),
  // route it to the composer. (The Slack/Gmail pattern.)
  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if (disabled) return
      if (e.key.length !== 1 || e.ctrlKey || e.metaKey || e.altKey) return
      const ae = document.activeElement as HTMLElement | null
      if (ae === textareaRef.current) return
      const tag = ae?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || ae?.isContentEditable) return
      // Focus the composer; the keystroke then lands here.
      textareaRef.current?.focus()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [disabled])

  function handleKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== 'Enter' || e.shiftKey) return
    e.preventDefault()
    // Cmd/Ctrl+Enter while streaming = Steer (cancel + send the typed
    // text as the replacement). Plain Enter = primary action (which
    // becomes Queue while streaming via onSend → enqueue mapping).
    if (streaming && (e.metaKey || e.ctrlKey) && onSteer) {
      const text = value.trim()
      if (!text) return
      onSteer(text)
      setValue('')
      return
    }
    submit()
  }

  function submit() {
    const text = value.trim()
    if (!text || disabled) return
    onSend(text)
    setValue('')
  }

  // Auto-grow on every value change — covers typing, paste, prefill,
  // programmatic clear, and resize-triggered recompute. Runs before
  // paint so the user never sees the textarea snap.
  useLayoutEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, MAX_GROW_PX()) + 'px'
  }, [value])

  // Recompute the cap on viewport resize (the max is vh-relative).
  useEffect(() => {
    function onResize() {
      const ta = textareaRef.current
      if (!ta) return
      ta.style.height = 'auto'
      ta.style.height = Math.min(ta.scrollHeight, MAX_GROW_PX()) + 'px'
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // Placeholder + textarea are always editable so the user can think +
  // type in parallel with the agent. While streaming, Enter queues the
  // message (handled upstream) and Cmd/Ctrl+Enter steers (cancel +
  // send). The big primary button on the right is rendered by
  // ChatPane, which morphs Send / Stop / Queue based on (streaming,
  // text); the small arrow button inside the composer box remains as
  // a click-to-send for users who don't like keyboards.
  const placeholder = streaming
    ? 'Type to queue a follow-up (Enter to queue, Cmd/Ctrl+Enter to steer)'
    : 'Message Guide (Enter to send, Shift+Enter for newline)'
  return (
    <div className="composer">
      <div className="composer__box">
        <textarea
          ref={textareaRef}
          className="composer__input"
          placeholder={placeholder}
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={handleKey}
          rows={1}
        />
        {streaming && onStop && (
          <button
            type="button"
            className="composer__stop"
            onClick={onStop}
            title="Stop the current turn (kills running work, drops queue)"
            aria-label="Stop"
          >
            <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor">
              <rect x="3" y="3" width="10" height="10" rx="1.5" />
            </svg>
          </button>
        )}
        <button
          className="composer__send"
          onClick={submit}
          disabled={!value.trim()}
          title={streaming ? 'Queue (Enter) — Cmd/Ctrl+Enter to steer' : 'Send (Enter)'}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
          </svg>
        </button>
      </div>
    </div>
  )
}
