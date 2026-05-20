import { useEffect, useRef, useState } from 'react'
import type { DisplayMessage, Entity } from '../types'
import { AGENTS, AgentGlyph } from './icons'
import Message from './Message'
import Composer from './Composer'
import TracePanel from './TracePanel'
import './ChatPane.css'

interface Props {
  messages: DisplayMessage[]
  streaming: boolean
  streamMsg: DisplayMessage | null
  onSend: (text: string) => void
  focusedEntity: Entity | null
  annotation?: { image: string; note: string } | null
  onClearAnnotation?: () => void
  prefill?: string
  onPrefillConsumed?: () => void
  composerFocus?: number
  onAnnotate?: (a: { image: string; note: string }) => void
  annotClear?: number
  onRetry?: () => void
}

export default function ChatPane({
  messages,
  streaming,
  streamMsg,
  onSend,
  focusedEntity,
  annotation,
  onClearAnnotation,
  prefill,
  onPrefillConsumed,
  composerFocus,
  onAnnotate,
  annotClear,
  onRetry,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [traceVisible, setTraceVisible] = useState(false)

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
    <div className={`chat-pane ${traceVisible ? 'chat-pane--split' : ''}`}>
      <div className="chat-tabs">
        {AGENTS.map((a, i) => (
          <span
            key={a.key}
            className={`chat-tab ${i === 0 ? 'chat-tab--active' : 'chat-tab--quiet'}`}
            title={i === 0 ? undefined : 'Coming soon'}
          >
            <span className="chat-tab__icon" style={{ color: a.color }}>
              <AgentGlyph agent={a.key} size={14} />
            </span>
            {a.name}
          </span>
        ))}
        <button
          type="button"
          className={`trace-toggle ${traceVisible ? 'trace-toggle--on' : ''}`}
          onClick={() => setTraceVisible(v => !v)}
          title="Show or hide the agent's inner loop"
        >
          <svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor">
            <path d="M3 5h14v2H3zM3 9h14v2H3zM3 13h10v2H3z" />
          </svg>
          Trace
        </button>
        <span
          className={`focus-chip ${focusedEntity && focusedEntity.type !== 'workspace' ? 'focus-chip--active' : ''}`}
          title="Conversation is scoped to this entity"
        >
          {focusChipText}
        </span>
      </div>

      <div className="chat-body">
        <div className="chat-main">
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
                hideToolBlocks={traceVisible}
                collapseTools={i !== all.length - 1}
                onAnnotate={onAnnotate}
                annotClear={annotClear}
                onRetry={!streaming && i === all.length - 1 ? onRetry : undefined}
              />
            ))}
          </div>
        </div>
        {traceVisible && <TracePanel messages={messages} streamMsg={streamMsg} />}
      </div>

      {annotation && (
        <div className="annot-attached">
          <img src={`data:image/png;base64,${annotation.image}`} alt="highlighted region" />
          <span>Focused on your highlight — ask about it (e.g. "what's here?"). Stays until you clear it.</span>
          <button onClick={onClearAnnotation} title="Clear highlight">×</button>
        </div>
      )}
      <Composer onSend={onSend} disabled={streaming}
                prefill={prefill} onPrefillConsumed={onPrefillConsumed}
                focusSignal={composerFocus} />
    </div>
  )
}
