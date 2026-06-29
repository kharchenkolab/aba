import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { DisplayMessage, Entity, PendingClarification, PendingApproval, Attachment } from '../types'
import { AGENTS, AgentGlyph } from '../components/icons'
// Per-message rendering dispatches through the lib message-renderer
// registry — ChatPane (platform infra) doesn't import from bio/
// directly (enforced by src/platform/__platform_imports.test.ts).
// Bio side registers ../bio/Message into the slot at startup.
import { message_renderer } from '../lib/messageRenderer'
import { type_label_for } from '../lib/typeLabels'
import Composer from './Composer'
import ErrorBoundary from './ErrorBoundary'
import './ChatPane.css'

interface Props {
  messages: DisplayMessage[]
  /** A chat search hit: scroll to + flash the bubble containing this
   *  messages-table row id. Cleared via onScrollConsumed once handled. */
  scrollToMsgId?: number | null
  onScrollConsumed?: () => void
  streaming: boolean
  /** True while a thread's history is being fetched — suppress the empty-state. */
  loading?: boolean
  streamMsg: DisplayMessage | null
  onSend: (text: string, attachments?: Attachment[]) => void
  /** Open the Data tab — the empty-project welcome's "create a dataset" action. */
  onOpenData?: () => void
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
  /** Option B / Phase 3: callback fired after a successful artifact-pin
   *  POST (inline chat figures whose entity hasn't been materialized
   *  yet). The parent should refresh + reveal the right rail. */
  onArtifactPinned?: () => void
  pinnedFigureIds?: Set<string>
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
  /** Queued follow-ups (set when user hits Enter while streaming). Drains one
   *  per completed turn. Each item carries its own attachments. */
  queuedMessages?: { text: string; attachments?: Attachment[] }[]
  /** Drop ALL queued messages (Clear all). */
  onDropQueue?: () => void
  /** Drop a single queued message by position (per-chip ✕). */
  onDropQueueAt?: (index: number) => void
  /** Steer — cancel current turn AND send `text` (+ attachments) as the replacement. */
  onSteer?: (text: string, attachments?: Attachment[]) => void
  /** Current thread id — used to persist the composer draft per thread AND as
   *  the per-thread scratch key for chat attachment uploads. */
  threadId?: string | null
  /** Per-request project pin — threaded into the composer's /api/attach
   *  uploads (mirrors the rest of the app's project_id flow). */
  projectId?: string
  /** #334 Phase 2 — current run_id, threaded to <Message> → <ToolStep> so an
   *  orphan tool_start can rehydrate live output via the buffer endpoint. */
  currentRunId?: string | null
}

export default function ChatPane({
  messages,
  scrollToMsgId,
  onScrollConsumed,
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
  onArtifactPinned,
  pinnedFigureIds,
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
  queuedMessages,
  onDropQueue,
  onDropQueueAt,
  onSteer,
  threadId,
  projectId,
  currentRunId,
  onOpenData,
}: Props) {
  const [clarifyDraft, setClarifyDraft] = useState('')
  useEffect(() => { if (!pendingClarification) setClarifyDraft('') }, [pendingClarification])
  const clarifyInputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (pendingClarification) clarifyInputRef.current?.focus()
  }, [pendingClarification])
  const scrollRef = useRef<HTMLDivElement>(null)
  // Scroll to + flash a chat search hit. Re-runs when `messages` arrive (the
  // thread switch loads history async, so the target bubble may not be in the
  // DOM on the first pass). No-op if the row isn't found (collapsed away /
  // different thread) — we still consume the request so it doesn't linger.
  useEffect(() => {
    if (scrollToMsgId == null) return
    const sel = `.msg-anchor[data-msg-ids~="${scrollToMsgId}"]`
    const el = scrollRef.current?.querySelector(sel) as HTMLElement | null
    if (!el) return   // not loaded yet — wait for the next messages update
    el.scrollIntoView({ block: 'center', behavior: 'smooth' })
    el.classList.add('msg-flash')
    const t = setTimeout(() => { el.classList.remove('msg-flash'); onScrollConsumed?.() }, 1700)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollToMsgId, messages])
  // "Pinned to bottom" auto-follow: while pinned, new content auto-scrolls
  // the view; while UN-pinned (the user has scrolled up to read history),
  // we DON'T jerk them back to the bottom — instead a floating button shows
  // up so they can return on their own. We count NEW MESSAGES (not stream
  // tokens) that landed while scrolled up so the button can show "N new
  // messages" instead of an abstract indicator.
  const isAtBottomRef = useRef(true)
  const lastMsgCountRef = useRef(0)
  const [showJump, setShowJump] = useState(false)
  const [newCount, setNewCount] = useState(0)
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

  // Track pinned-to-bottom from scroll events. Threshold (60px) accommodates
  // small layout shifts (an image loading, code block expanding) without
  // un-pinning the user just because the bottom moved a little.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => {
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight
      const atBottom = dist < 60
      isAtBottomRef.current = atBottom
      if (atBottom) {
        setShowJump(false)
        setNewCount(0)
        lastMsgCountRef.current = messages.length + (streamMsg ? 1 : 0)
      } else {
        setShowJump(true)
      }
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [messages.length, streamMsg])

  // Auto-scroll only when the user is pinned. While unpinned, count how many
  // brand-new MESSAGES (not stream tokens — stream replaces an in-progress
  // assistant message in place) landed since we left the bottom.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    if (isAtBottomRef.current) {
      el.scrollTop = el.scrollHeight
      lastMsgCountRef.current = messages.length
    } else {
      const delta = messages.length - lastMsgCountRef.current
      if (delta > 0) setNewCount(c => c + delta)
      lastMsgCountRef.current = messages.length
    }
  }, [messages, streamMsg])

  // Bottom of new figure (or any deferred-layout content) isn't visible after
  // the initial autoscroll, because images load AFTER the message hits the DOM
  // and grow the layout AFTER we set scrollTop. Re-snap whenever any message's
  // rendered size grows — but ONLY if the user was still pinned at the moment,
  // so reading mid-thread is never yanked. (Browsers don't fire `scroll` on
  // pure content growth, so isAtBottomRef stays at whatever it was the last
  // time the user actually scrolled.)
  useEffect(() => {
    const el = scrollRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    let raf = 0
    const ro = new ResizeObserver(() => {
      if (raf) return    // coalesce — many images can finish on the same frame
      raf = requestAnimationFrame(() => {
        raf = 0
        if (isAtBottomRef.current) el.scrollTop = el.scrollHeight
      })
    })
    const observeChildren = () => { Array.from(el.children).forEach(c => ro.observe(c)) }
    observeChildren()
    const mo = new MutationObserver(observeChildren)
    mo.observe(el, { childList: true })
    return () => { ro.disconnect(); mo.disconnect(); if (raf) cancelAnimationFrame(raf) }
  }, [])

  const jumpToBottom = () => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    // Optimistic flip — the scroll listener will confirm/correct.
    isAtBottomRef.current = true
    setShowJump(false)
    setNewCount(0)
    lastMsgCountRef.current = messages.length
  }

  // Any user-initiated send (composer, plan Go, scenario chip, etc.) should
  // re-pin to the bottom even if the user was scrolled up reading history —
  // typing a new message implies "I want to see what comes next".
  const sendAndPin = useCallback((text: string, attachments?: Attachment[]) => {
    isAtBottomRef.current = true
    setShowJump(false)
    setNewCount(0)
    onSend(text, attachments)
    // Nudge on the next frame so the new optimistic user message lands
    // in view immediately; the auto-scroll effect handles subsequent
    // stream tokens.
    requestAnimationFrame(() => {
      const el = scrollRef.current
      if (el) el.scrollTop = el.scrollHeight
    })
  }, [onSend])

  const all = streamMsg ? [...messages, streamMsg] : messages
  // Empty project (no user content yet, sitting on the workspace root) → show a
  // Welcome with two get-started actions instead of the bare "Ask Guide about
  // your data". NB: a fresh project is NOT entities.length===0 — it always carries
  // the capability catalog (~48), a default thread, and an analysis. "Empty" means
  // no data/results: no datasets, claims, results, or figures.
  const isEmptyProject = !(entities || []).some(
    e => e.type === 'dataset' || e.type === 'data' || e.type === 'claim'
      || e.type === 'result' || e.type === 'figure')
    && (!focusedEntity || focusedEntity.type === 'workspace')
  // basename → {url, kind} map for inline filename mentions in agent prose.
  // Walk every tool_result and pick the LAST-SEEN entry per basename (later
  // messages win on collision — most recent run's output wins). Covers
  // plots, tables, and the new `files` bucket (PDFs / RDS / HTML / …).
  const fileMap = useMemo(() => {
    const m = new Map<string, { url: string; kind: 'plot' | 'table' | 'file' }>()
    type Entry = { url?: unknown; original_name?: unknown }
    const ingest = (arr: unknown, kind: 'plot' | 'table' | 'file') => {
      if (!Array.isArray(arr)) return
      for (const e of arr as Entry[]) {
        const name = typeof e?.original_name === 'string' ? e.original_name : null
        const url = typeof e?.url === 'string' ? e.url : null
        if (name && url) m.set(name, { url, kind })
      }
    }
    for (const msg of all) {
      for (const b of msg.blocks) {
        if (b.type !== 'tool_result') continue
        const r = (b as { result?: Record<string, unknown> }).result || {}
        ingest(r.plots, 'plot')
        ingest(r.tables, 'table')
        ingest(r.files, 'file')
      }
    }
    return m
  }, [all])
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
              isEmptyProject ? (
                <div className="chat-empty chat-welcome">
                  <p className="chat-welcome__hi">Welcome — let's get started.</p>
                  <p className="chat-welcome__sub">Bring in some data, or just ask Guide a question.</p>
                  <div className="chat-welcome__actions">
                    {onOpenData && (
                      <button className="chat-welcome__action" onClick={onOpenData}>
                        <span className="chat-welcome__icon" aria-hidden>＋</span>
                        <span className="chat-welcome__body">
                          <b>Create a dataset</b>
                          <span className="chat-welcome__hint">in the Data tab</span>
                        </span>
                      </button>
                    )}
                    <button className="chat-welcome__action" disabled={streaming}
                      onClick={() => sendAndPin('Show samples for GEO study GSE192391')}>
                      <span className="chat-welcome__icon" aria-hidden>✦</span>
                      <span className="chat-welcome__body">
                        <b>Ask a question</b>
                        <span className="chat-welcome__hint">e.g. “Show samples for GEO study GSE192391”</span>
                      </span>
                    </button>
                  </div>
                </div>
              ) : (
                <div className="chat-empty">
                  <p>
                    {focusedEntity && focusedEntity.type !== 'workspace'
                      ? `Ask Guide about this ${(type_label_for(focusedEntity.type) ?? focusedEntity.type).toLowerCase()}.`
                      : 'Ask Guide about your data.'}
                  </p>
                </div>
              )
            )}
            {all.map((m, i) => {
              const Message = message_renderer()
              if (!Message) return null   // bio hasn't loaded yet; chat will refresh
              return (
                <div key={m.id} className="msg-anchor"
                     data-msg-ids={(m.dbIds || []).join(' ') || undefined}>
                <ErrorBoundary label="message"
                  fallback={reset => (
                    <div className="errbound">
                      <span className="errbound__text">This message couldn’t be displayed.</span>
                      <button className="errbound__retry" onClick={reset}>Retry</button>
                    </div>
                  )}>
                <Message
                  key={m.id}
                  message={m}
                  fileMap={fileMap}
                  currentRunId={currentRunId}
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
                  onArtifactPinned={onArtifactPinned}
                  pinnedFigureIds={pinnedFigureIds}
                  keptKeys={keptKeys}
                  onKeepMessage={onKeepMessage}
                  planActive={!streaming && i === all.length - 1 && m.role === 'assistant'}
                  onPlanGo={(saveAsRun: boolean) => sendAndPin(saveAsRun
                    ? 'Go ahead with the plan as proposed.'
                    : 'Go ahead with the plan as proposed. Do not save this as a run.')}
                  onPlanAdjust={() => setExtraFocus(n => n + 1)}
                />
                </ErrorBoundary>
                </div>
              )
            })}
            {starters && starters.length > 0 && !all.some(m => m.role === 'user') && (
              <div className="chat-starters">
                {starters.map((s, i) => (
                  <button key={i} className="chat-starter" disabled={streaming}
                          onClick={() => sendAndPin(`${s} — plan it first.`)}
                          title="Ask the Guide to do this (it'll show a plan first)">
                    <span className="chat-starter__spark">✦</span>{s}
                  </button>
                ))}
              </div>
            )}
          </div>
          {showJump && (
            <button
              type="button"
              className={`chat-jump-bottom ${newCount > 0 ? 'chat-jump-bottom--new' : ''}`}
              onClick={jumpToBottom}
              title={newCount > 0 ? 'New messages — jump to latest' : 'Jump to latest'}
              aria-label="Jump to latest"
            >
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none"
                   stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="6 9 12 15 18 9" />
              </svg>
              {newCount > 0 && (
                <span className="chat-jump-bottom__label">
                  {newCount} new {newCount === 1 ? 'message' : 'messages'}
                </span>
              )}
            </button>
          )}
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
            {queuedMessages && queuedMessages.length > 0 && (
              <div className="queue-chips" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {queuedMessages.map((msg, i) => (
                  <div className="queue-chip" key={i}>
                    <span className="queue-chip__label">
                      {queuedMessages.length > 1 ? `Queued ${i + 1}:` : 'Queued:'}
                    </span>
                    <span className="queue-chip__text">
                      {msg.text || (msg.attachments?.length ? `(${msg.attachments.length} attachment${msg.attachments.length > 1 ? 's' : ''})` : '')}
                    </span>
                    <div className="queue-chip__actions">
                      {/* "Send now" only on the head — steer interrupts the
                          current turn to send the NEXT message immediately. */}
                      {streaming && onSteer && i === 0 && (
                        <button className="queue-chip__send"
                                onClick={() => { onDropQueueAt?.(0); onSteer(msg.text, msg.attachments) }}
                                title="Stop the current turn and send this now">
                          Send now
                        </button>
                      )}
                      {onDropQueueAt && (
                        <button className="queue-chip__cancel" onClick={() => onDropQueueAt(i)}
                                title="Drop this queued message">
                          Cancel
                        </button>
                      )}
                    </div>
                  </div>
                ))}
                {queuedMessages.length > 1 && onDropQueue && (
                  <button className="queue-chips__clear" onClick={onDropQueue}
                          title="Drop all queued messages"
                          style={{ alignSelf: 'flex-start', fontSize: '0.8em', opacity: 0.7 }}>
                    Clear all ({queuedMessages.length})
                  </button>
                )}
              </div>
            )}
            <Composer
              onSend={sendAndPin}
              disabled={false}
              streaming={streaming}
              onSteer={onSteer}
              onStop={onStop}
              prefill={prefill}
              onPrefillConsumed={onPrefillConsumed}
              focusSignal={(composerFocus ?? 0) + extraFocus}
              draftKey={`chatdraft:${threadId ?? 'default'}`}
              projectId={projectId}
              threadId={threadId ?? 'default'}
            />
          </div>
        )}
      </div>
    </div>
  )
}
