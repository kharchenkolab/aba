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
  /** Stable per-thread key for persisting the unsent draft across unmounts
   *  (switching tabs/views) so typed-but-unsent text isn't lost. */
  draftKey?: string
}

export default function Composer({ onSend, disabled, prefill, onPrefillConsumed, focusSignal, streaming, onSteer, onStop, draftKey }: Props) {
  // Restore any persisted draft for this thread (the composer unmounts when you
  // switch views, so without this the unsent text would vanish).
  const [value, setValue] = useState<string>(() => (draftKey && sessionStorage.getItem(draftKey)) || '')
  // Visual feedback for Stop: pressed → "stopping" pulse until the backend's
  // cancelled-SSE arrives and the parent flips `streaming` to false. Without
  // this the click had no immediate visual signal — the user thought the
  // button didn't register and kept hammering it.
  const [stopping, setStopping] = useState(false)
  useEffect(() => { if (!streaming) setStopping(false) }, [streaming])
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const persist = (v: string) => {
    if (!draftKey) return
    try { if (v) sessionStorage.setItem(draftKey, v); else sessionStorage.removeItem(draftKey) }
    catch { /* storage unavailable — degrade to in-memory only */ }
  }
  const setDraft = (v: string) => { setValue(v); persist(v) }

  // ── Sent-message history: shell-style ↑/↓ recall, per thread ──
  const histKey = (k: string) => k.replace(/^chatdraft:/, 'chathist:')
  const [history, setHistory] = useState<string[]>(() => {
    try { return draftKey ? JSON.parse(sessionStorage.getItem(histKey(draftKey)) || '[]') : [] }
    catch { return [] }
  })
  const histIdx = useRef<number | null>(null)   // null = editing a draft, not navigating
  const histStash = useRef('')                   // the in-progress draft, stashed on entering history
  const caretToEnd = useRef(false)               // park caret at end after a programmatic value set
  const pushHistory = (text: string) => {
    histIdx.current = null
    setHistory(h => {
      const next = h[h.length - 1] === text ? h : [...h, text]   // skip consecutive dupes
      const capped = next.slice(-100)
      if (draftKey) { try { sessionStorage.setItem(histKey(draftKey), JSON.stringify(capped)) } catch { /* ignore */ } }
      return capped
    })
  }

  // Reload the draft + history if the thread (draftKey) changes without a remount.
  //
  // Subtle: when the user opens a project on the URL placeholder threadId
  // ('default'), draftKey is 'chatdraft:default'. As soon as a real thread
  // entity resolves (entity-list load, or server creates one mid-turn),
  // draftKey upgrades to 'chatdraft:thr_xyz' WHILE the user is typing into
  // the still-mounted composer. Without the guard below, this effect would
  // see no saved data at the new key and clobber the user's typing with ''.
  // The original text would be safe in sessionStorage[chatdraft:default],
  // but it'd be orphaned — feels like the textarea ate the keystrokes
  // (sporadic "what I typed just disappears" bug, 2026-06-04).
  //
  // Guard: if the previous draftKey was a ':default' placeholder AND the
  // new key has no saved data AND we have typing in flight, MIGRATE rather
  // than clobber. Genuine thread-to-thread switches (no placeholder in the
  // prev key) still load the new thread's saved draft as expected.
  const firstDraftKey = useRef(true)
  const prevDraftKey = useRef(draftKey)
  const valueRef = useRef(value); valueRef.current = value
  useEffect(() => {
    const prev = prevDraftKey.current
    prevDraftKey.current = draftKey
    if (firstDraftKey.current) { firstDraftKey.current = false; return }
    const newSaved = draftKey ? sessionStorage.getItem(draftKey) : null
    const wasPlaceholder = !!prev && prev.endsWith(':default')
    if (newSaved != null) {
      setValue(newSaved)
    } else if (wasPlaceholder && valueRef.current) {
      // Carry typing-in-flight across the placeholder→real upgrade.
      if (draftKey) { try { sessionStorage.setItem(draftKey, valueRef.current) } catch { /* */ } }
      // setValue is intentionally NOT called — the existing live value stays.
    } else {
      setValue('')
    }
    histIdx.current = null
    try { setHistory(draftKey ? JSON.parse(sessionStorage.getItem(histKey(draftKey)) || '[]') : []) }
    catch { setHistory([]) }
  }, [draftKey])

  // When an advisor's "Try it" prefills the composer, drop the text in and
  // focus — the user can edit or just hit Enter.
  useEffect(() => {
    if (prefill) {
      histIdx.current = null
      setDraft(prefill)
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
    // Shell-style history recall. ↑ only fires on the first line and ↓ only on
    // the last line, so multi-line editing (and caret movement) still works.
    if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
      const ta = textareaRef.current
      if (!ta || !history.length) return
      if (e.key === 'ArrowUp') {
        const onFirstLine = value.slice(0, ta.selectionStart).indexOf('\n') === -1
        if (!onFirstLine) return
        e.preventDefault()
        if (histIdx.current === null) { histStash.current = value; histIdx.current = history.length - 1 }
        else if (histIdx.current > 0) { histIdx.current -= 1 }
        caretToEnd.current = true
        setDraft(history[histIdx.current])
      } else {
        if (histIdx.current === null) return            // not navigating → let ↓ move the caret
        const onLastLine = value.slice(ta.selectionEnd).indexOf('\n') === -1
        if (!onLastLine) return
        e.preventDefault()
        if (histIdx.current < history.length - 1) {
          histIdx.current += 1
          caretToEnd.current = true
          setDraft(history[histIdx.current])
        } else {                                        // past the newest → restore the stashed draft
          histIdx.current = null
          caretToEnd.current = true
          setDraft(histStash.current)
        }
      }
      return
    }
    if (e.key !== 'Enter' || e.shiftKey) return
    e.preventDefault()
    // Cmd/Ctrl+Enter while streaming = Steer (cancel + send the typed
    // text as the replacement). Plain Enter = primary action (which
    // becomes Queue while streaming via onSend → enqueue mapping).
    if (streaming && (e.metaKey || e.ctrlKey) && onSteer) {
      const text = value.trim()
      if (!text) return
      pushHistory(text)
      onSteer(text)
      setDraft('')
      return
    }
    submit()
  }

  function submit() {
    const text = value.trim()
    if (!text || disabled) return
    pushHistory(text)
    onSend(text)
    setDraft('')
    // Defense: the useLayoutEffect on [value] SHOULD clear the inline
    // height when value transitions to '', but during rapid streaming
    // render cycles the textarea has been seen to retain its stretched
    // height (PK 2026-06-07 + 2026-06-08). RAF schedules a forced
    // reset AFTER React's batch commits.
    requestAnimationFrame(() => {
      const ta = textareaRef.current
      if (ta && !ta.value) ta.style.height = ''
    })
  }

  // Auto-grow on every value change — covers typing, paste, prefill,
  // programmatic clear, and resize-triggered recompute. Runs before
  // paint so the user never sees the textarea snap.
  //
  // Also re-runs on `streaming` change: the placeholder text differs
  // between idle and streaming modes, and the auto-grow's first
  // computation can land at the post-prefill stretched height if it
  // happened before React's commit settled.
  //
  // Empty-value branch: skip the scrollHeight measurement entirely and
  // just CLEAR the inline height, letting CSS (`min-height: 22px` +
  // `rows={1}`) govern the natural single-line size. The measurement
  // path had an intermittent bug — in narrow-column layouts the
  // useLayoutEffect would read a stale scrollHeight between commit and
  // paint, leaving the textarea stuck at the previous MAX_GROW_PX
  // height even though the value was empty. Symptom (PK 2026-06-08):
  // empty textarea sitting at ~half column height; typing then
  // recomputed correctly and the textarea visibly shrunk to fit. By
  // not measuring at all when value is empty, the only state worth
  // recomputing is the non-empty case where the value's content
  // actually drives height.
  useLayoutEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    if (!value) {
      ta.style.height = ''
      return
    }
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, MAX_GROW_PX()) + 'px'
    // After a programmatic value set (history recall), park the caret at the end.
    if (caretToEnd.current) {
      caretToEnd.current = false
      const n = ta.value.length
      ta.setSelectionRange(n, n)
    }
  }, [value, streaming])

  // Recompute the cap on viewport resize (the max is vh-relative).
  // Same empty-value short-circuit as the useLayoutEffect above.
  useEffect(() => {
    function onResize() {
      const ta = textareaRef.current
      if (!ta) return
      if (!ta.value) { ta.style.height = ''; return }
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
          onChange={e => { histIdx.current = null; setDraft(e.target.value) }}
          onKeyDown={handleKey}
          rows={1}
        />
        {streaming && onStop && (
          <button
            type="button"
            className="composer__stop"
            onClick={() => { setStopping(true); onStop() }}
            disabled={stopping}
            title={stopping
              ? "Stop requested — waiting for the agent to land at a cancel point"
              : "Stop the current turn (kills running work, drops queue)"}
            aria-label={stopping ? 'Stopping' : 'Stop'}
          >
            {/* Octagonal stop sign — solid red octagon, thin white
                border (mimics the real sign), white "STOP" wordmark
                centered. Bold + condensed letter spacing so it stays
                legible at ~22 px. */}
            <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
              <polygon
                points="7.5,1 16.5,1 23,7.5 23,16.5 16.5,23 7.5,23 1,16.5 1,7.5"
                fill="currentColor"
                stroke="#fff"
                strokeWidth="1.2"
                strokeLinejoin="round"
              />
              <text
                x="12" y="12.3"
                textAnchor="middle"
                dominantBaseline="central"
                fill="#fff"
                fontFamily="-apple-system, system-ui, sans-serif"
                fontWeight="900"
                fontSize="6"
                letterSpacing="-0.3"
              >STOP</text>
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
