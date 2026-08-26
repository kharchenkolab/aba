/**
 * Reattaching to a turn in flight must not re-animate it.
 *
 * Switching into a thread whose turn is still running rebuilds that turn's
 * state by replaying its event log — necessary, because mid-turn assistant
 * output is persisted nowhere else. But replaying it VISIBLY re-runs the show:
 * the tool the turn is working on appears to START AGAIN, every time you look
 * at it. Reported live twice (2026-08-26), on a turn that was quietly
 * installing a package for minutes.
 *
 * The fix is not to stop replaying — it is to stop PAINTING the replay. The
 * handlers fold every replayed event into state as before; nothing renders
 * until the server's `caught_up` marker names the backlog/live boundary. That
 * marker has to come from the server: "no events for a while" is not a
 * boundary, because a turn mid-install is legitimately silent for minutes.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/** The paint-suppression rule, extracted exactly as useChat applies it. */
function foldStream(events: { type: string; replayed?: number; name?: string }[],
                    reattaching: boolean) {
  const blocks: string[] = []
  const paints: string[][] = []
  let replaying = reattaching
  const paint = () => { if (!replaying) paints.push([...blocks]) }
  for (const ev of events) {
    if (ev.type === 'caught_up') {
      replaying = false
      if (ev.replayed) paints.push([...blocks])
      continue
    }
    if (ev.type === 'tool_start') { blocks.push(`tool:${ev.name}`); paint() }
    else if (ev.type === 'delta') { blocks.push('text'); paint() }
  }
  return { blocks, paints }
}

describe('reattach catch-up', () => {
  it('paints ONCE for a replayed backlog, not once per event', () => {
    const { blocks, paints } = foldStream([
      { type: 'tool_start', name: 'ensure_capability' },
      { type: 'delta' },
      { type: 'delta' },
      { type: 'caught_up', replayed: 3 },
    ], true)
    expect(blocks).toHaveLength(3)          // state fully rebuilt
    expect(paints).toHaveLength(1)          // and shown once, already caught up
    expect(paints[0]).toEqual(['tool:ensure_capability', 'text', 'text'])
  })

  it('streams normally once caught up', () => {
    const { paints } = foldStream([
      { type: 'tool_start', name: 'a' },
      { type: 'caught_up', replayed: 1 },
      { type: 'delta' },
      { type: 'delta' },
    ], true)
    // one catch-up paint + one per live event
    expect(paints).toHaveLength(3)
  })

  it('a fresh turn is never buffered', () => {
    // WIDE: the same loop runs for a normal POST /api/chat, which must animate
    // exactly as before — a regression here would freeze all live output.
    const { paints } = foldStream([
      { type: 'tool_start', name: 'a' },
      { type: 'delta' },
      { type: 'caught_up', replayed: 0 },
      { type: 'delta' },
    ], false)
    expect(paints).toHaveLength(3)
  })

  it('a reattach with an empty backlog costs no extra render', () => {
    const { paints } = foldStream([
      { type: 'caught_up', replayed: 0 },
      { type: 'delta' },
    ], true)
    expect(paints).toHaveLength(1)
  })

  it('never drops an event from the rebuilt state', () => {
    // DEGENERATE: suppressing the PAINT must not suppress the FOLD — the whole
    // point is that the state is complete when it finally renders.
    const { blocks } = foldStream([
      { type: 'tool_start', name: 'a' }, { type: 'delta' },
      { type: 'tool_start', name: 'b' }, { type: 'delta' },
      { type: 'caught_up', replayed: 4 },
    ], true)
    expect(blocks).toEqual(['tool:a', 'text', 'tool:b', 'text'])
  })
})


describe('the rule above is the one useChat actually applies', () => {
  // The fold above is a COPY of useChat's loop, and a copy can drift until it
  // certifies nothing. These bind it to the real file.
  const src = readFileSync(resolve(process.cwd(), 'src/useChat.ts'), 'utf8')

  it('routes every streaming render through the suppressor', () => {
    const loop = src.slice(src.indexOf("if (ev.type === 'caught_up')"),
                           src.indexOf('const done = '))
    const direct = loop.match(/setStreamMsg\(\{ id: assistantId/g) || []
    // exactly one direct render is allowed: the catch-up flush itself
    expect(direct.length).toBe(1)
    expect(loop).toContain('paint({ id: assistantId')
  })

  it('buffers only on a reattach', () => {
    expect(src).toContain('let replaying = !!opts.reattachRunId')
  })

  it('ends buffering on the server marker, not on a timer', () => {
    expect(src).toContain("ev.type === 'caught_up'")
    // read the BRANCH, not a byte window — a fixed-width slice fails the day
    // someone adds a comment inside it, which is a test breaking on prose
    const from = src.indexOf("if (ev.type === 'caught_up')")
    const branch = src.slice(from, src.indexOf("} else if", from))
    expect(branch).toContain('replaying = false')
    expect(branch).not.toContain('setTimeout')
  })
})
