import { describe, it, expect, beforeEach } from 'vitest'
import { installErrorRecorder, recentErrors, recentErrorLines, __resetForTest } from './errorLog'

// Fresh EventTarget per test so listeners don't accumulate on the real window
// (and construct events plainly to avoid ErrorEvent/PromiseRejectionEvent ctor
// differences across jsdom versions).
function fireError(t: EventTarget, message: string, filename?: string, lineno?: number, colno?: number) {
  const ev = new Event('error') as Event & Record<string, unknown>
  ev.message = message
  if (filename) { ev.filename = filename; ev.lineno = lineno; ev.colno = colno }
  t.dispatchEvent(ev)
}
function fireRejection(t: EventTarget, reason: unknown) {
  const ev = new Event('unhandledrejection') as Event & Record<string, unknown>
  ev.reason = reason
  t.dispatchEvent(ev)
}

describe('errorLog', () => {
  let target: EventTarget
  beforeEach(() => { __resetForTest(); target = new EventTarget() })

  it('captures window error events with source', () => {
    installErrorRecorder(target)
    fireError(target, 'boom', 'a.js', 4, 2)
    const e = recentErrors()
    expect(e.length).toBe(1)
    expect(e[0].kind).toBe('error')
    expect(e[0].message).toBe('boom')
    expect(e[0].source).toBe('a.js:4:2')
  })

  it('caps the ring buffer at 30 (drops oldest)', () => {
    installErrorRecorder(target)
    for (let i = 0; i < 40; i++) fireError(target, 'e' + i)
    expect(recentErrors().length).toBe(30)
    const lines = recentErrorLines()
    expect(lines[lines.length - 1]).toContain('e39')   // newest kept
    expect(lines[0]).toContain('e10')                  // first 10 dropped
  })

  it('installs listeners only once (no duplicate capture)', () => {
    installErrorRecorder(target)
    installErrorRecorder(target)            // 2nd call is a no-op
    fireError(target, 'once')
    expect(recentErrors().length).toBe(1)
  })

  it('captures unhandled promise rejections', () => {
    installErrorRecorder(target)
    fireRejection(target, new Error('nope'))
    expect(recentErrors().some(x => x.kind === 'unhandledrejection' && x.message === 'nope')).toBe(true)
  })

  it('is idle until an error fires (empty buffer at rest)', () => {
    installErrorRecorder(target)
    expect(recentErrors()).toEqual([])
  })
})
