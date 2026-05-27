import { useEffect, useRef, useState } from 'react'
import type { DisplayMessage, Entity, PendingClarification, PendingApproval } from '../types'
import { AGENTS, AgentGlyph } from './icons'
import Message from './Message'
import Composer from './Composer'
import ErrorBoundary from './ErrorBoundary'
import './ChatPane.css'

interface Props {
  messages: DisplayMessage[]
  streaming: boolean
  /** True while a thread's history is being fetched — suppress the empty-state. */
  loading?: boolean
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
  entities?: Entity[]
  onPin?: (id: string, pinned: boolean) => void
  keptKeys?: Set<string>
  onKeepMessage?: (key: string, text: string, imageUrls: string[], pinned: boolean) => void
  /** Selection→Claim: make a claim from a highlighted span of the conversation. */
  onClaimFromSelection?: (text: string) => void
  /** Highlight mode, lifted so the canvas header can own the toggle (chat-first).
   *  Falls back to internal state when not provided (legacy non-embedded use). */
  highlighting?: boolean
  onHighlightingChange?: (on: boolean) => void
  /** Cold-start starter prompts (from the Guide's orientation). Shown above the
   *  composer until the user starts talking; clicking one sends it. */
  starters?: string[]
  /** B1 — if set, the Guide is awaiting a one-line clarification answer.
   *  Replaces the main composer with a focused mini-composer that posts
   *  to /api/turns/{run_id}/resume. */
  pendingClarification?: PendingClarification | null
  onAnswerClarification?: (text: string) => void
  /** P1 #3 — if set, a flagged tool wants explicit approval before
   *  running. Replaces the main composer with an Approve / Reject bar. */
  pendingApproval?: PendingApproval | null
  onRespondApproval?: (action: 'approve' | 'approve_session' | 'reject') => void
  /** Stop the current turn (cancel + kill any running work). */
  onStop?: () => void
  /** Currently-queued message (set when user hits Enter while streaming). */
  queuedMessage?: string | null
  /** Drop the queued message without sending. */
  onDropQueue?: () => void
  /** Steer — cancel current turn AND send `text` as the replacement. */
  onSteer?: (text: string) => void
}

export default function ChatPane({
  messages,
  streaming,
  loading,
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
  entities,
  onPin,
  keptKeys,
  onKeepMessage,
  onClaimFromSelection,
  highlighting: highlightingProp,
  onHighlightingChange,
  starters,
  pendingClarification,
  onAnswerClarification,
  pendingApproval,
  onRespondApproval,
  onStop,
  queuedMessage,
  onDropQueue,
  onSteer,
}: Props) {
  const [clarifyDraft, setClarifyDraft] = useState('')
  useEffect(() => { if (!pendingClarification) setClarifyDraft('') }, [pendingClarification])
  const clarifyInputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (pendingClarification) clarifyInputRef.current?.focus()
  }, [pendingClarification])
  const scrollRef = useRef<HTMLDivElement>(null)
  const [hlLocal, setHlLocal] = useState(false)
  const highlighting = highlightingProp ?? hlLocal
  const setHighlighting = (on: boolean) => (onHighlightingChange ?? setHlLocal)(on)
  const [anyDrawing, setAnyDrawing] = useState(false)
  const [extraFocus, setExtraFocus] = useState(0)   // bump to focus the composer (plan "Adjust")
  const [sel, setSel] = useState<{ text: string; x: number; y: number } | null>(null)

  // Selection→Claim: when the user highlights a span of the conversation, offer
  // to crystallize it into a claim (ui3 P3).
  useEffect(() => {
    if (!onClaimFromSelection) return
    function onUp() {
      const s = window.getSelection()
      const text = s?.toString().trim() ?? ''
      const node = s && s.rangeCount ? s.anchorNode : null
      const host = node?.nodeType === 3 ? node.parentElement : (node as Element | null)
      if (!text || text.length < 8 || !host || !scrollRef.current?.contains(host)) { setSel(null); return }
      const r = s!.getRangeAt(0).getBoundingClientRect()
      setSel({ text, x: r.left + r.width / 2, y: r.top })
    }
    document.addEventListener('mouseup', onUp)
    return () => document.removeEventListener('mouseup', onUp)
  }, [onClaimFromSelection])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [messages, streamMsg])

  const all = streamMsg ? [...messages, streamMsg] : messages
  const scoped = !!focusedEntity && focusedEntity.type !== 'workspace'
  const focusChipText = scoped ? `${focusedEntity!.type} · ${focusedEntity!.title}` : 'Workspace'

  const hlBtn = (
    <button
      type="button"
      className={`hl-toggle ${highlighting ? 'hl-toggle--on' : ''}`}
      onClick={() => setHighlighting(!highlighting)}
      title={highlighting ? 'Cancel highlight' : 'Highlight a region of any message to ask Guide about it'}
    >
      <svg viewBox="0 0 24 24" width="14" height="14" fill="#fde047" stroke="#a16207" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M15.6 2.6a2 2 0 012.8 0l3 3a2 2 0 010 2.8l-9 9-5.2 1.2 1.2-5.2 9-9zM5 19h14v2H5z"/></svg>
    </button>
  )

  return (
    <div className={`chat-pane ${embedded ? 'chat-pane--embedded' : ''}`}>
      {embedded ? (
        // Compact peek (entity-first): a slim label so the user knows this chat
        // is scoped to the open entity. Chat-first primary gets NO header — the
        // conversation flows directly; the highlighter lives in the canvas head.
        compact ? (
          <div className="panel-head">
            <div className="panel-head-title primary">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
              Chat about this
            </div>
          </div>
        ) : null
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
          <span className={`focus-chip ${scoped ? 'focus-chip--active' : ''}`}>{focusChipText}</span>
          {hlBtn}
        </div>
      )}

      <div className="chat-body">
        <div className="chat-main">
          <div className="chat-scroll" ref={scrollRef}>
            {all.length === 0 && !loading && (
              <div className="chat-empty">
                <p>
                  {focusedEntity && focusedEntity.type !== 'workspace'
                    ? `Ask Guide about this ${focusedEntity.type === 'analysis' ? 'run' : focusedEntity.type}.`
                    : 'Ask Guide about your data.'}
                </p>
              </div>
            )}
            {all.map((m, i) => (
              <ErrorBoundary key={m.id} label="message"
                fallback={reset => (
                  <div className="errbound">
                    <span className="errbound__text">This message couldn’t be displayed.</span>
                    <button className="errbound__retry" onClick={reset}>Retry</button>
                  </div>
                )}>
              <Message
                key={m.id}
                message={m}
                isStreaming={streaming && i === all.length - 1 && m.role === 'assistant'}
                collapseTools={i !== all.length - 1}
                onAnnotate={onAnnotate}
                highlighting={highlighting}
                anyDrawing={anyDrawing}
                onDrawingChange={setAnyDrawing}
                onHighlightDone={() => setHighlighting(false)}
                annotClear={annotClear}
                onRetry={!streaming && i === all.length - 1 ? onRetry : undefined}
                entities={entities}
                onPin={onPin}
                keptKeys={keptKeys}
                onKeepMessage={onKeepMessage}
                planActive={!streaming && i === all.length - 1 && m.role === 'assistant'}
                onPlanGo={() => onSend('Go ahead with the plan as proposed.')}
                onPlanAdjust={() => setExtraFocus(n => n + 1)}
              />
              </ErrorBoundary>
            ))}
            {starters && starters.length > 0 && !all.some(m => m.role === 'user') && (
              <div className="chat-starters">
                {starters.map((s, i) => (
                  <button key={i} className="chat-starter" disabled={streaming}
                          onClick={() => onSend(`${s} — plan it first.`)}
                          title="Ask the Guide to do this (it'll show a plan first)">
                    <span className="chat-starter__spark">✦</span>{s}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {sel && onClaimFromSelection && (
        <button className="chat-claim-sel" style={{ left: Math.max(90, sel.x), top: Math.max(56, sel.y - 38) }}
          onMouseDown={e => e.preventDefault()}
          onClick={() => { onClaimFromSelection(sel.text); window.getSelection()?.removeAllRanges(); setSel(null) }}>
          ✦ Claim from selection
        </button>
      )}

      {annotation && (
        <div className="annot-attached">
          <img src={`data:image/png;base64,${annotation.image}`} alt="highlighted region" />
          <span>Focused on your highlight — ask about it (e.g. "what's here?"). Stays until you clear it.</span>
          <button onClick={onClearAnnotation} title="Clear highlight">×</button>
        </div>
      )}
      <div className="composer-wrap">
        {pendingApproval && onRespondApproval ? (
          <div className="approval-bar">
            <div className="approval-bar__q">
              Allow <code>{pendingApproval.toolName}</code>? <span className="approval-bar__sum">{pendingApproval.summary}</span>
            </div>
            <div className="approval-bar__actions">
              <button className="approval-bar__btn approval-bar__btn--reject"
                      onClick={() => onRespondApproval('reject')} disabled={streaming}>Reject</button>
              <button className="approval-bar__btn approval-bar__btn--approve"
                      onClick={() => onRespondApproval('approve')} disabled={streaming}>Approve</button>
              {pendingApproval.policy === 'session' && (
                <button className="approval-bar__btn approval-bar__btn--always"
                        onClick={() => onRespondApproval('approve_session')} disabled={streaming}>
                  Allow this thread
                </button>
              )}
            </div>
          </div>
        ) : pendingClarification && onAnswerClarification ? (
          <form className="clarify-bar"
                onSubmit={e => {
                  e.preventDefault()
                  const text = clarifyDraft.trim()
                  if (!text) return
                  onAnswerClarification(text)
                  setClarifyDraft('')
                }}>
            <div className="clarify-bar__q">{pendingClarification.question}</div>
            <input ref={clarifyInputRef}
                   className="clarify-bar__input"
                   placeholder="Your answer…"
                   value={clarifyDraft}
                   onChange={e => setClarifyDraft(e.target.value)}
                   disabled={streaming} />
            <button type="submit" className="clarify-bar__send" disabled={streaming || !clarifyDraft.trim()}>
              Send
            </button>
          </form>
        ) : (
          <div className="composer-with-stop">
            {/* Queue chip — shown above the composer when the user has
                committed a follow-up while the agent is still
                responding. Cancel drops it; "Send now" fires Steer
                (cancel + send) so the queue runs immediately. */}
            {queuedMessage && (
              <div className="queue-chip">
                <span className="queue-chip__label">Queued:</span>
                <span className="queue-chip__text">{queuedMessage}</span>
                <div className="queue-chip__actions">
                  {streaming && onSteer && (
                    <button className="queue-chip__send" onClick={() => onSteer(queuedMessage)}
                            title="Stop the current turn and send this now">
                      Send now
                    </button>
                  )}
                  {onDropQueue && (
                    <button className="queue-chip__cancel" onClick={onDropQueue} title="Drop the queued message">
                      Cancel
                    </button>
                  )}
                </div>
              </div>
            )}
            <Composer
              onSend={onSend}
              disabled={false}
              streaming={streaming}
              onSteer={onSteer}
              onStop={onStop}
              prefill={prefill}
              onPrefillConsumed={onPrefillConsumed}
              focusSignal={(composerFocus ?? 0) + extraFocus}
            />
          </div>
        )}
      </div>
    </div>
  )
}
