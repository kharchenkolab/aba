/**
 * useChat — a stream failure must not erase the turn.
 *
 * Regression (live, 2026-07-26): the backend was restarted while a long
 * multi-step turn was streaming. The live turn renders from `streamMsg`, and the
 * error path cleared it before appending the error — so a plan execution's whole
 * visible output (text, plots, tool steps) vanished, leaving only the user's
 * opening message and a bare "Couldn't reach the server. TypeError: network
 * error". The work was on the server the entire time; only the view was lost,
 * and there was nothing to read or continue from.
 *
 * Contract verified here:
 *   1. Blocks produced before the failure are COMMITTED into `messages` (same
 *      as the `done` path), not discarded.
 *   2. The error is appended AFTER them, so it reads as the end of the turn.
 *   3. `streaming` is released and `streamMsg` cleared, so the composer unlocks
 *      and `retryLast` can continue.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useChat } from './useChat'

/** SSE stream that DELIVERS `events` (one per pull, so the consumer actually
 *  processes each), then FAILS — what a backend bounce looks like to the
 *  browser: an error on the reader, not a clean close. Erroring inside `start`
 *  would tear the stream down before the enqueued chunks are ever read. */
function failingSSEStream(events: string[]) {
  const enc = new TextEncoder()
  let i = 0
  return new ReadableStream({
    pull(controller) {
      if (i < events.length) {
        controller.enqueue(enc.encode(`data: ${events[i++]}\n\n`))
        return
      }
      controller.error(new TypeError('network error'))
    },
  })
}

beforeEach(() => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : (input as Request).url
    const json = (b: string) =>
      new Response(b, { status: 200, headers: { 'Content-Type': 'application/json' } })
    if (url.includes('/api/messages')) return json('[]')
    if (url.includes('/active-turn')) return json('null')
    if (url.includes('/api/jobs')) return json('[]')
    if (url.endsWith('/api/chat') && init?.method === 'POST') {
      // A turn that produces real output, then the connection dies. No run_id in
      // the manifest, so the reattach path is not taken — this is the terminal
      // failure case the user sees.
      return new Response(
        failingSSEStream([
          JSON.stringify({ type: 'delta', text: 'Step 1: loaded the inputs.' }),
          JSON.stringify({ type: 'delta', text: ' Step 2: integrated them.' }),
        ]),
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    }
    return json('{}')
  })
})
afterEach(() => { vi.restoreAllMocks() })

describe('useChat — stream failure preserves the turn', () => {
  it('commits the produced blocks, then appends the error', async () => {
    const { result } = renderHook(() =>
      useChat('workspace', undefined, null, 0, 'thr_err', 'prj_err'))

    await act(async () => { result.current.sendMessage('Go ahead with the plan as proposed.') })

    await waitFor(() => {
      expect(result.current.streaming).toBe(false)
    }, { timeout: 2000 })

    const msgs = result.current.messages
    const text = JSON.stringify(msgs)

    // 1. the work survives — THE regression (it used to be dropped with streamMsg)
    expect(text).toContain('Step 1: loaded the inputs.')
    expect(text).toContain('Step 2: integrated them.')

    // 2. the error is present, and LAST — the end of the turn, not a replacement
    const errIdx = msgs.findIndex(m =>
      m.blocks.some(b => (b as { type?: string }).type === 'error'))
    expect(errIdx).toBeGreaterThan(-1)
    expect(errIdx).toBe(msgs.length - 1)
    const workIdx = msgs.findIndex(m => JSON.stringify(m.blocks).includes('Step 1'))
    expect(workIdx).toBeGreaterThan(-1)
    expect(workIdx).toBeLessThan(errIdx)

    // 3. the user's own message is still there, and the UI is unlocked so the
    //    turn can be retried/continued
    expect(text).toContain('Go ahead with the plan as proposed.')
    expect(result.current.streamMsg).toBeNull()
    expect(result.current.streaming).toBe(false)
  })
})
