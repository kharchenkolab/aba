/**
 * The follow-up queue is PER THREAD, survives Stop, and never jumps its own
 * order. Drives the REAL hook — an earlier queue-adjacent guard tested an
 * extracted copy of the logic and stayed green while the shipped module was
 * broken.
 *
 * Live report (2026-08-27), reproduced step for step below:
 *   "I queued two messages, then switched threads — the queued messages kept
 *    showing when I was looking at another (executing) thread. Switched back
 *    to the original thread .. the turn finished there, but the queued
 *    messages stayed as they were - queued. I then tried to type in another
 *    message - hoping it would bump the queue - but that new message started
 *    executing (bypassing queued) ... I hit stop, then it stopped and both
 *    queued messages disappeared"
 *
 * Four defects in one sequence: a queue shared across threads, a queue that
 * never drains once its turn ends out of view, a send that overtakes the
 * queue, and a Stop that silently destroys typed text.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useChat } from './useChat'

const sent: { text: string; thread: string }[] = []
let activeTurn: string | null = null

function sse(events: object[]) {
  return new ReadableStream({
    start(c) {
      const enc = new TextEncoder()
      for (const e of events) c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`))
      c.close()
    },
  })
}

beforeEach(() => {
  sent.length = 0
  activeTurn = null
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : (input as Request).url
    const json = (b: string) => new Response(b, { status: 200,
      headers: { 'Content-Type': 'application/json' } })
    if (url.includes('/api/messages')) return json('[]')
    if (url.includes('/active-turn'))
      return json(activeTurn ? JSON.stringify({ run_id: activeTurn }) : 'null')
    if (url.includes('/api/jobs')) return json('[]')
    if (url.endsWith('/api/chat') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      sent.push({ text: body.text, thread: body.thread_id })
      return new Response(sse([{ type: 'manifest', run_id: 'run_q', manifest: {} },
                               { type: 'done' }]),
                          { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    }
    return json('{}')
  })
})
afterEach(() => vi.restoreAllMocks())

const render = (tid: string) => renderHook(
  ({ t }: { t: string }) => useChat('workspace', undefined, null, 0, t, 'prj_q'),
  { initialProps: { t: tid } })

describe('useChat — the follow-up queue', () => {
  it('a queue belongs to its thread and does not follow you to another', async () => {
    const { result, rerender } = render('thr_leak')
    await waitFor(() => expect(result.current.loading).toBe(false))
    act(() => { result.current.enqueue('first'); result.current.enqueue('second') })
    expect(result.current.queuedMessages.map(q => q.text)).toEqual(['first', 'second'])

    // switch to a thread that is mid-turn (so nothing drains there)
    activeTurn = 'run_other'
    await act(async () => { rerender({ t: 'thr_B' }) })
    expect(result.current.queuedMessages).toEqual([])   // B's queue: empty

    // and coming back restores A's, rather than having lost it
    activeTurn = 'run_still_going'
    await act(async () => { rerender({ t: 'thr_leak' }) })
    expect(result.current.queuedMessages.map(q => q.text)).toEqual(['first', 'second'])
  })

  it('drains when you return to a thread whose turn has since finished', async () => {
    const { result, rerender } = render('thr_drain')
    await waitFor(() => expect(result.current.loading).toBe(false))
    act(() => { result.current.enqueue('first'); result.current.enqueue('second') })

    activeTurn = 'run_other'
    await act(async () => { rerender({ t: 'thr_B' }) })
    activeTurn = null                                   // A's turn ended while away
    await act(async () => { rerender({ t: 'thr_drain' }) })

    await waitFor(() => expect(sent.map(s => s.text)).toContain('first'))
    expect(sent[0]).toEqual({ text: 'first', thread: 'thr_drain' })
  })

  it('a new message goes BEHIND the queue, never ahead of it', async () => {
    // Hold the FIRST turn's stream OPEN. The drain chains on `done` — with the
    // default instantly-closing mock, 'second' could legitimately fire before
    // the assertions ran, so this test was flaky by construction (observed:
    // queue ['third'] because two drains completed). Mid-drain state is only
    // assertable while the first turn is provably in flight.
    let release!: () => void
    let held = false
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>)
      .mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : (input as Request).url
        const json = (b: string) => new Response(b, { status: 200,
          headers: { 'Content-Type': 'application/json' } })
        if (url.includes('/api/messages')) return json('[]')
        if (url.includes('/active-turn')) return json('null')
        if (url.includes('/api/jobs')) return json('[]')
        if (url.endsWith('/api/chat') && init?.method === 'POST') {
          sent.push({ text: JSON.parse(String(init.body)).text,
                      thread: JSON.parse(String(init.body)).thread_id })
          const enc = new TextEncoder()
          if (!held) {
            held = true
            const body = new ReadableStream({
              start(c) {
                c.enqueue(enc.encode(
                  `data: ${JSON.stringify({ type: 'manifest', run_id: 'run_f', manifest: {} })}\n\n`))
                release = () => {
                  c.enqueue(enc.encode('data: {"type":"done"}\n\n'))
                  c.close()
                }
              },
            })
            return new Response(body, { status: 200,
              headers: { 'Content-Type': 'text/event-stream' } })
          }
          return new Response(sse([{ type: 'manifest', run_id: 'run_q', manifest: {} },
                                   { type: 'done' }]),
                              { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
        }
        return json('{}')
      })
    const { result } = render('thr_fifo')
    await waitFor(() => expect(result.current.loading).toBe(false))
    act(() => { result.current.enqueue('first'); result.current.enqueue('second') })
    await act(async () => { await result.current.sendMessage('third') })
    // the oldest went out first and is STILL in flight (held) — 'third' must
    // not overtake anything
    await waitFor(() => expect(sent.map(s => s.text)).toEqual(['first']))
    expect(result.current.queuedMessages.map(q => q.text)).toEqual(['second', 'third'])
    // release the held turn: the rest drains in strict FIFO
    act(() => release())
    await waitFor(() => expect(sent.map(s => s.text)).toEqual(['first', 'second', 'third']))
    await waitFor(() => expect(result.current.queuedMessages).toEqual([]))
  })

  it('Stop ends the turn and KEEPS what you typed', async () => {
    // A stream held OPEN, so the queue is typed while the turn is genuinely
    // in flight — the only window in which Stop can destroy it. An earlier
    // version of this test enqueued after the stream had already closed and
    // was green against the destructive code.
    let emit!: (e: object) => void
    let close!: () => void
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>)
      .mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : (input as Request).url
        const json = (b: string) => new Response(b, { status: 200,
          headers: { 'Content-Type': 'application/json' } })
        if (url.includes('/api/messages')) return json('[]')
        if (url.includes('/active-turn')) return json('null')
        if (url.includes('/api/jobs')) return json('[]')
        if (url.endsWith('/api/chat') && init?.method === 'POST') {
          sent.push({ text: JSON.parse(String(init.body)).text,
                      thread: JSON.parse(String(init.body)).thread_id })
          const enc = new TextEncoder()
          const body = new ReadableStream({
            start(c) {
              emit = (e: object) => c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`))
              close = () => c.close()
              emit({ type: 'manifest', run_id: 'run_c', manifest: {} })
            },
          })
          return new Response(body, { status: 200,
            headers: { 'Content-Type': 'text/event-stream' } })
        }
        return json('{}')
      })
    const { result } = render('thr_stop')
    await waitFor(() => expect(result.current.loading).toBe(false))
    act(() => { void result.current.sendMessage('running') })
    await waitFor(() => expect(result.current.streaming).toBe(true))

    // type two follow-ups WHILE it runs, then Stop
    act(() => { result.current.enqueue('kept one'); result.current.enqueue('kept two') })
    expect(result.current.queuedMessages).toHaveLength(2)
    await act(async () => { emit({ type: 'cancelled' }); close() })
    await waitFor(() => expect(result.current.streaming).toBe(false))

    expect(result.current.queuedMessages.map(q => q.text))
      .toEqual(['kept one', 'kept two'])
    // and Stop did NOT send them either — it ends the turn, nothing more
    expect(sent.map(s => s.text)).not.toContain('kept one')
    expect(sent.map(s => s.text)).not.toContain('kept two')
  })

  it('the per-chip ✕ and Clear still work (dropping stays deliberate)', async () => {
    const { result } = render('thr_drop')
    await waitFor(() => expect(result.current.loading).toBe(false))
    act(() => { result.current.enqueue('a'); result.current.enqueue('b') })
    act(() => { result.current.dropQueueAt(0) })
    expect(result.current.queuedMessages.map(q => q.text)).toEqual(['b'])
    act(() => { result.current.dropQueue() })
    expect(result.current.queuedMessages).toEqual([])
  })
})
