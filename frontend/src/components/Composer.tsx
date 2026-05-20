import { useState, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import './Composer.css'

interface Props {
  onSend: (text: string) => void
  disabled: boolean
}

export default function Composer({ onSend, disabled }: Props) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

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
