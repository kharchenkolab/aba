import { useEffect, useRef } from 'react'
import type { DisplayMessage, Entity } from '../types'
import Message from './Message'
import Composer from './Composer'
import './ChatPane.css'

interface Props {
  messages: DisplayMessage[]
  streaming: boolean
  streamMsg: DisplayMessage | null
  onSend: (text: string) => void
  focusedEntity: Entity | null
}

export default function ChatPane({
  messages,
  streaming,
  streamMsg,
  onSend,
  focusedEntity,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [messages, streamMsg])

  const all = streamMsg ? [...messages, streamMsg] : messages
  const focusChipText =
    !focusedEntity || focusedEntity.type === 'workspace'
      ? 'Workspace'
      : `${focusedEntity.type} · ${focusedEntity.title}`

  return (
    <div className="chat-pane">
      <div className="chat-tabs">
        <span className="chat-tab chat-tab--active">
          <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor" style={{ color: 'var(--guide)' }}>
            <path d="M10 2a8 8 0 100 16A8 8 0 0010 2zm0 3a1.5 1.5 0 110 3 1.5 1.5 0 010-3zm0 10c-2.2 0-4.1-1.1-5.3-2.8.7-1.1 2.9-1.7 5.3-1.7s4.6.6 5.3 1.7C14.1 13.9 12.2 15 10 15z"/>
          </svg>
          Guide
        </span>
        <span className="chat-tab chat-tab--quiet" title="Coming soon">Methodologist</span>
        <span className="chat-tab chat-tab--quiet" title="Coming soon">Skeptic</span>
        <span className="chat-tab chat-tab--quiet" title="Coming soon">Explorer</span>
        <span className="chat-tab chat-tab--quiet" title="Coming soon">Stylist</span>
        <span
          className={`focus-chip ${focusedEntity && focusedEntity.type !== 'workspace' ? 'focus-chip--active' : ''}`}
          title="Conversation is scoped to this entity"
        >
          {focusChipText}
        </span>
      </div>

      <div className="chat-scroll" ref={scrollRef}>
        {all.length === 0 && (
          <div className="chat-empty">
            <p>
              {focusedEntity && focusedEntity.type !== 'workspace'
                ? `Ask Guide about this ${focusedEntity.type}.`
                : 'Ask Guide about your data.'}
            </p>
          </div>
        )}
        {all.map((m, i) => (
          <Message
            key={m.id}
            message={m}
            isStreaming={streaming && i === all.length - 1 && m.role === 'assistant'}
          />
        ))}
      </div>

      <Composer onSend={onSend} disabled={streaming} />
    </div>
  )
}
