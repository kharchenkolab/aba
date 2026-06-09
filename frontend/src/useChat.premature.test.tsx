/**
 * useChat — premature stream-close recovery.
 *
 * Regression: when uvicorn's worker reloads mid-stream (typically
 * triggered by an `ensure_capability` pip install touching
 * envs/pylib/...), the SSE connection closes without emitting a
 * terminal event (`done`/`cancel`/`error`). Before this fix, the
 * read loop would break on `done: true` and silently exit — leaving
 * the UI's `streaming` flag stuck at `true` forever. Diagnosed
 * 2026-06-09 in prj_0ea773b4.
 *
 * Contract verified here:
 *   1. When the stream closes WITHOUT a terminal event, `streaming`
 *      eventually becomes false (UI is released).
 *   2. The recovery automatically tries to reattach to the same
 *      run_id (via the same fetch path with `reattachRunId`), so the
 *      Turn that's still running on the server picks up where it
 *      left off.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useChat } from './useChat'


function makeSSEStream(events: string[], opts: { closeEarly?: boolean } = {}) {
  // Helper: build a ReadableStream that emits the given SSE events,
  // then either sends `done: true` (closeEarly) or keeps the
  // connection alive forever. The test below uses closeEarly: true
  // with NO terminal event in `events` to simulate a worker reload.
  return new ReadableStream({
    start(controller) {
      const enc = new TextEncoder()
      for (const e of events) {
        controller.enqueue(enc.encode(`data: ${e}\n\n`))
      }
      if (opts.closeEarly) {
        controller.close()
      }
      // Else: stream stays open; reader.read() never returns done.
    },
  })
}


beforeEach(() => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : (input as Request).url
    // Initial history fetch + active-turn probe: return empty.
    if (url.includes('/api/messages')) {
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    if (url.includes('/active-turn')) {
      return new Response('null', { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    if (url.includes('/api/jobs')) {
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    // The /api/chat POST: respond with an SSE stream that sends a
    // manifest (carrying run_id, so the recovery has something to
    // reattach with), then closes WITHOUT a terminal event.
    if (url.endsWith('/api/chat') && init?.method === 'POST') {
      const body = makeSSEStream(
        [JSON.stringify({ type: 'manifest', run_id: 'run_test_xyz', manifest: {} })],
        { closeEarly: true },
      )
      return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    }
    // The reattach GET — assert it's called with the right run_id +
    // since seq. We give back a benign stream that immediately closes
    // with a `done` so the recovery's recursive call ends cleanly.
    if (url.includes('/api/turns/') && url.includes('/stream')) {
      const body = makeSSEStream(
        [JSON.stringify({ type: 'done' })],
        { closeEarly: true },
      )
      return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
})
afterEach(() => {
  vi.restoreAllMocks()
})


describe('useChat — premature stream close', () => {
  it('clears streaming and auto-reattaches when the stream closes without a terminal event', async () => {
    const { result } = renderHook(() => useChat('workspace', undefined, null, 0, 'thr_test', 'prj_test'))

    // Fire the chat send (sendMessage is sync-ish: it returns
    // before the fetch resolves, so we don't await it here — but we
    // do let microtasks flush via act()).
    await act(async () => { result.current.sendMessage('hello') })

    // The reattach setTimeout fires after 250ms; allow for the SSE
    // pump too. waitFor polls until the assertions pass.
    await waitFor(() => {
      expect(result.current.streaming).toBe(false)
    }, { timeout: 2000 })

    await waitFor(() => {
      const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      const reattachCalled = calls.some(
        ([input]: [unknown]) => {
          const url = typeof input === 'string' ? input : ((input as Request).url ?? '')
          return url.includes('/api/turns/run_test_xyz/stream')
        },
      )
      expect(reattachCalled).toBe(true)
    }, { timeout: 2000 })
  })

  it('does NOT reattach if a terminal "done" event arrived', async () => {
    // Override the /api/chat mock to send a terminal event before close.
    ;(globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/messages')) return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.includes('/active-turn')) return new Response('null', { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.includes('/api/jobs')) return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.endsWith('/api/chat') && init?.method === 'POST') {
        const body = makeSSEStream(
          [
            JSON.stringify({ type: 'manifest', run_id: 'run_test_clean', manifest: {} }),
            JSON.stringify({ type: 'done' }),
          ],
          { closeEarly: true },
        )
        return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      }
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
    })

    const { result } = renderHook(() => useChat('workspace', undefined, null, 0, 'thr_test', 'prj_test'))
    await act(async () => { result.current.sendMessage('hello') })

    await waitFor(() => expect(result.current.streaming).toBe(false), { timeout: 2000 })

    // Give the (would-be) setTimeout 250ms a chance to fire — it must
    // NOT, since the `done` event already ran the clean exit.
    await new Promise(r => setTimeout(r, 400))

    const reattachCalled = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some(
      ([input]: [unknown]) => {
        const url = typeof input === 'string' ? input : ((input as Request).url ?? '')
        return url.includes('/api/turns/run_test_clean/stream')
      },
    )
    expect(reattachCalled).toBe(false)
  })
})
