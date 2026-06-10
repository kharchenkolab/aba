/**
 * Tests for the pure readSSEStream helper.
 *
 * Four invariants verified:
 *   1. Clean close (terminal event fires before stream ends)        -> 'done'
 *   2. Premature close (stream closes WITHOUT a terminal event)     -> 'premature'
 *   3. Caller-side cancel (abortSignal fires mid-stream)             -> 'cancelled'
 *   4. Parse-error tolerance (malformed `data:` line skipped, loop continues)
 */
import { describe, expect, it } from 'vitest'
import { readSSEStream } from './sseReader'


function makeStream(frames: string[], opts: { closeAfter?: boolean; holdOpen?: boolean } = {}) {
  // Builds a Response.body-shaped ReadableStream that emits each frame
  // as an SSE `data: <frame>\n\n` chunk, then either closes (default)
  // or stays open forever (when holdOpen).
  return new ReadableStream({
    start(controller) {
      const enc = new TextEncoder()
      for (const f of frames) {
        controller.enqueue(enc.encode(`data: ${f}\n\n`))
      }
      if (opts.closeAfter !== false && !opts.holdOpen) controller.close()
    },
  })
}


describe('readSSEStream', () => {
  it('returns "done" when onEvent returns terminal before stream ends', async () => {
    const events: unknown[] = []
    const r = await readSSEStream({
      fetcher: async () => new Response(
        makeStream([
          JSON.stringify({ type: 'manifest', run_id: 'r1' }),
          JSON.stringify({ type: 'delta', text: 'hi' }),
          JSON.stringify({ type: 'done' }),
          // Server may queue more bytes; the helper drains + cancels:
          JSON.stringify({ type: 'extra-noise' }),
        ]),
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      ),
      onEvent: (ev) => {
        events.push(ev)
        const t = (ev as { type?: string }).type
        if (t === 'done' || t === 'cancelled' || t === 'error') return 'terminal'
      },
    })
    expect(r).toBe('done')
    // Should have stopped at 'done' — extra-noise must not be delivered.
    const types = events.map(e => (e as { type?: string }).type)
    expect(types).toEqual(['manifest', 'delta', 'done'])
  })

  it('returns "premature" when stream closes without a terminal event', async () => {
    const events: unknown[] = []
    const r = await readSSEStream({
      fetcher: async () => new Response(
        makeStream([
          JSON.stringify({ type: 'manifest', run_id: 'r2' }),
          JSON.stringify({ type: 'delta', text: 'hi' }),
        ]),
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      ),
      onEvent: (ev) => { events.push(ev) },
    })
    expect(r).toBe('premature')
    expect((events[0] as { type?: string }).type).toBe('manifest')
  })

  it('returns "cancelled" when abortSignal fires mid-stream', async () => {
    const ac = new AbortController()
    let eventCount = 0
    const p = readSSEStream({
      // holdOpen: stream stays alive; only the abort terminates it.
      fetcher: async () => new Response(
        makeStream([JSON.stringify({ type: 'manifest', run_id: 'r3' })], { holdOpen: true }),
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      ),
      onEvent: () => { eventCount += 1 },
      abortSignal: ac.signal,
    })
    // Give it a tick to start reading, then abort.
    await new Promise(r => setTimeout(r, 30))
    expect(eventCount).toBeGreaterThanOrEqual(1)
    ac.abort()
    const r = await p
    expect(r).toBe('cancelled')
  })

  it('tolerates malformed data: lines and keeps reading', async () => {
    const events: unknown[] = []
    const r = await readSSEStream({
      fetcher: async () => new Response(
        new ReadableStream({
          start(c) {
            const enc = new TextEncoder()
            // Valid, then garbage, then valid + done.
            c.enqueue(enc.encode(`data: ${JSON.stringify({ type: 'a' })}\n\n`))
            c.enqueue(enc.encode(`data: {this is not valid json\n\n`))
            c.enqueue(enc.encode(`data: ${JSON.stringify({ type: 'b' })}\n\n`))
            c.enqueue(enc.encode(`data: ${JSON.stringify({ type: 'done' })}\n\n`))
            c.close()
          },
        }),
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      ),
      onEvent: (ev) => {
        events.push(ev)
        if ((ev as { type?: string }).type === 'done') return 'terminal'
      },
    })
    expect(r).toBe('done')
    // Garbage frame skipped; only 'a', 'b', 'done' delivered.
    expect(events.map(e => (e as { type?: string }).type)).toEqual(['a', 'b', 'done'])
  })
})
