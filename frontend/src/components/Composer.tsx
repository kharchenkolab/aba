import { useState, useRef, useEffect } from 'react'
import type { KeyboardEvent } from 'react'
import './Composer.css'

interface Props {
  onSend: (text: string) => void
  disabled: boolean
  prefill?: string
  onPrefillConsumed?: () => void
  /** Increment to focus the composer (e.g. after a highlight is attached). */
  focusSignal?: number
}

export default function Composer({ onSend, disabled, prefill, onPrefillConsumed, focusSignal }: Props) {
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
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  function submit() {
    const text = value.trim()
    if (!text || disabled) return
    onSend(text)
    setValue('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  function handleInput() {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'
  }

  return (
    <div className="composer">
      <div className={`composer__box ${disabled ? 'composer__box--disabled' : ''}`}>
        <textarea
          ref={textareaRef}
          className="composer__input"
          placeholder={disabled ? 'Guide is responding…' : 'Message Guide (Enter to send, Shift+Enter for newline)'}
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={handleKey}
          onInput={handleInput}
          disabled={disabled}
          rows={1}
        />
        <button
          className="composer__send"
          onClick={submit}
          disabled={disabled || !value.trim()}
          title="Send (Enter)"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
          </svg>
        </button>
      </div>
    </div>
  )
}
