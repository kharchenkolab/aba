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
  /** Posture-shell mode: drop the advisor tabs (header carries them) and
   *  show a slim panel-head + focus context chip above the composer. */
  embedded?: boolean
  /** Compact peek variant (chat about an entity, narrow). */
  compact?: boolean
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
  embedded,
  compact,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [traceVisible, setTraceVisible] = useState(false)

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [messages, streamMsg])

  const all = streamMsg ? [...messages, streamMsg] : messages
  const scoped = !!focusedEntity && focusedEntity.type !== 'workspace'
  const focusChipText = scoped ? `${focusedEntity!.type} · ${focusedEntity!.title}` : 'Workspace'

  return (
    <div className={`chat-pane ${traceVisible ? 'chat-pane--split' : ''} ${embedded ? 'chat-pane--embedded' : ''}`}>
      {embedded ? (
        <div className="panel-head">
          <div className="panel-head-title primary">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            {compact ? 'Chat about this' : 'Thread chat'}
          </div>
          <div className="panel-head-actions">
            <span className="panel-head-sub">{all.length} msg</span>
            <button
              type="button"
              className={`trace-toggle ${traceVisible ? 'trace-toggle--on' : ''}`}
              onClick={() => setTraceVisible(v => !v)}
              title="Show or hide the agent's inner loop"
            >
              <svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor"><path d="M3 5h14v2H3zM3 9h14v2H3zM3 13h10v2H3z" /></svg>
              Trace
            </button>
          </div>
        </div>
      ) : (
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
            <svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor"><path d="M3 5h14v2H3zM3 9h14v2H3zM3 13h10v2H3z" /></svg>
            Trace
          </button>
          <span className={`focus-chip ${scoped ? 'focus-chip--active' : ''}`}>{focusChipText}</span>
        </div>
      )}

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
      <div className="composer-wrap">
        {embedded && scoped && (
          <div className="composer-chips">
            <span className="chip">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="18" height="18" rx="2"/></svg>
              {focusedEntity!.type} · {focusedEntity!.title}
            </span>
          </div>
        )}
        <Composer onSend={onSend} disabled={streaming}
                  prefill={prefill} onPrefillConsumed={onPrefillConsumed}
                  focusSignal={composerFocus} />
      </div>
    </div>
  )
}
