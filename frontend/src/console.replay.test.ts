/**
 * Reattaching to a live turn must not re-log it in the Console.
 *
 * Reported live 2026-08-26, TWICE — the second time after I had "fixed" it:
 * "every time I switch threads (which are running) the log (Console) repeats
 * entries."
 *
 * The first fix suppressed the CHAT bubble's repaint during catch-up and did
 * nothing about the Console, which is fed by a different call — `noteTurnEvent`
 * runs unconditionally on every event, before any of that logic. So the chat
 * stopped re-animating and the Console kept duplicating, which is what the
 * report actually described.
 *
 * Dedupe rather than skip: a client joining a turn mid-flight (a reload, a
 * second tab) has never seen those events and SHOULD log them. Only a SECOND
 * sighting of the same (run_id, seq) is dropped.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { noteTurnEvent, consoleRows, clearConsole } from './console'
import type { SSEEvent } from './wire'

const ev = (seq: number, name = 'ensure_capability'): SSEEvent =>
  ({ type: 'tool_start', name, input: {}, tool_use_id: `t${seq}`, seq } as unknown as SSEEvent)

describe('console dedupe on reattach', () => {
  beforeEach(() => { clearConsole?.() })

  it('logs a turn once, however many times it is replayed', () => {
    const before = consoleRows().length
    for (const s of [1, 2, 3]) noteTurnEvent(ev(s), 'run_A')
    const afterFirst = consoleRows().length
    // switch away and back: the whole backlog is replayed
    for (const s of [1, 2, 3]) noteTurnEvent(ev(s), 'run_A')
    // ...and again
    for (const s of [1, 2, 3]) noteTurnEvent(ev(s), 'run_A')
    expect(afterFirst - before).toBe(3)
    expect(consoleRows().length).toBe(afterFirst)
  })

  it('still logs a turn the client has never seen', () => {
    // WIDE: joining mid-flight (reload / second tab) must not lose history
    const before = consoleRows().length
    for (const s of [7, 8]) noteTurnEvent(ev(s), 'run_FRESH')
    expect(consoleRows().length - before).toBe(2)
  })

  it('does not collapse the same seq across DIFFERENT runs', () => {
    // every turn starts its seq at 1; keying on seq alone would hide turn 2
    const before = consoleRows().length
    noteTurnEvent(ev(1), 'run_ONE')
    noteTurnEvent(ev(1), 'run_TWO')
    expect(consoleRows().length - before).toBe(2)
  })

  it('logs an unkeyed event rather than dropping it', () => {
    // DEGENERATE: no seq (or no run) — silence would be worse than a repeat
    const before = consoleRows().length
    noteTurnEvent({ type: 'tool_start', name: 'x', input: {},
                    tool_use_id: 'z' } as unknown as SSEEvent, 'run_A')
    expect(consoleRows().length - before).toBe(1)
  })
})
