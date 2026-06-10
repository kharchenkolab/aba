/**
 * readSSEStream — pure, testable SSE event consumer.
 *
 * Background: useChat.ts:runStream's per-event read loop is ~400 LOC
 * tangled with state mutation, retry, reattach, and recovery. The
 * 2026-06-09 frontend-hang bug lived inside that tangle. Wave 2 #4
 * extracts the I/O loop so the state machine consumes a clean stream
 * of parsed events.
 *
 * Contract:
 * - Caller supplies `fetcher(signal)` that returns a Response with an
 *   SSE body. The function owns the AbortController; cancellation can
 *   come from `abortSignal` (caller-owned) or stop returning 'terminal'
 *   from onEvent.
 * - Returns a terminal reason:
 *     'done'       — onEvent returned 'terminal' (caller saw done/cancel/error)
 *     'cancelled'  — caller's abortSignal fired
 *     'premature'  — stream closed cleanly (done: true) but no terminal event
 *     'error'      — fetch/parse exception (re-thrown by caller decision: rejects)
 * - Malformed `data:` lines are skipped (matches the existing runStream
 *   tolerance — Vite's HMR pings + occasional partial flushes shouldn't
 *   abort the loop).
 * - No React, no global state. The whole module is a single function
 *   plus the terminal-reason type alias.
 */


export type SSETerminal = 'done' | 'cancelled' | 'premature'


export interface ReadSSEOptions {
  /** Fetch the SSE stream. Caller decides URL, method, headers, body.
   *  The function passes its own AbortSignal — caller MUST honor it
   *  (chain into fetch's `signal` option). */
  fetcher: (signal: AbortSignal) => Promise<Response>

  /** Called for every parsed `data: <obj>` event. Caller's per-event
   *  state machine. Return 'terminal' to stop the loop early — that's
   *  how the caller signals "the event I just saw was the end-of-turn"
   *  (e.g. ev.type === 'done' / 'cancel' / 'error'). */
  onEvent: (ev: unknown) => void | 'terminal'

  /** Optional caller-owned signal to abort the stream mid-flight
   *  (thread switch, page unload, etc.). When this signal aborts,
   *  the function resolves to 'cancelled'. */
  abortSignal?: AbortSignal
}


export async function readSSEStream(opts: ReadSSEOptions): Promise<SSETerminal> {
  const ac = new AbortController()
  // Link the caller's abortSignal into our internal one so a caller-side
  // cancel triggers the same code path as our own internal aborts.
  if (opts.abortSignal) {
    if (opts.abortSignal.aborted) {
      ac.abort()
    } else {
      opts.abortSignal.addEventListener('abort', () => ac.abort(), { once: true })
    }
  }

  let res: Response
  try {
    res = await opts.fetcher(ac.signal)
  } catch (e) {
    if (ac.signal.aborted) return 'cancelled'
    throw e
  }
  if (!res.body) throw new Error('readSSEStream: response has no body')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let terminalSeen = false

  // Wire abort -> reader.cancel(). Without this, reader.read() blocks
  // forever when the underlying ReadableStream stays open — the abort
  // signal alone doesn't propagate into the reader's pending promise.
  // reader.cancel() rejects the pending read() with an AbortError-like
  // error which our try/catch handles via the `aborted` check.
  const onAbort = () => { reader.cancel().catch(() => {}) }
  if (ac.signal.aborted) onAbort()
  else ac.signal.addEventListener('abort', onAbort, { once: true })

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      if (ac.signal.aborted) {
        await reader.cancel().catch(() => {})
        return 'cancelled'
      }
      buf += decoder.decode(value, { stream: true })
      // SSE frames are \n-delimited; we may receive partial frames so
      // pop the last (possibly incomplete) item back into the buffer.
      const lines = buf.split('\n')
      buf = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const raw = line.slice(6).trim()
        if (!raw) continue
        let ev: unknown
        try {
          ev = JSON.parse(raw)
        } catch {
          // Malformed line — log-and-skip is the existing useChat
          // behavior; preserve it so a transient bad frame doesn't
          // abort the whole turn.
          continue
        }
        const verdict = opts.onEvent(ev)
        if (verdict === 'terminal') {
          terminalSeen = true
          // Drain remaining bytes politely — the server may have more
          // queued but the caller said it's done.
          await reader.cancel().catch(() => {})
          return 'done'
        }
      }
    }
  } catch (e) {
    if (ac.signal.aborted) return 'cancelled'
    throw e
  }

  // Reader returned done: true cleanly. Possibilities:
  //   - aborted via our onAbort() that called reader.cancel() — return 'cancelled'.
  //   - We saw a terminal event before the close — return 'done'.
  //   - Otherwise the server closed without a terminal event — 'premature'.
  if (ac.signal.aborted) return 'cancelled'
  return terminalSeen ? 'done' : 'premature'
}
