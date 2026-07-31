/**
 * Console feed store — grouping, ring cap, notification mapping, facet
 * filtering.
 *
 * Armed where it matters: the tool-lifecycle test asserts ONE row total (a
 * regression to row-per-event fails the count, not just the content), and
 * the ring-cap test pins both sides (old evicted AND new kept).
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  consoleRows, consoleSites, filterRows, noteNotification, noteTurnEvent,
  resetConsole, subscribeConsole,
} from './console'
import type { SSEEvent, NotificationEvent } from './wire'

const start = (id: string, name = 'run_code', input: Record<string, unknown> = {}): SSEEvent =>
  ({ type: 'tool_start', name, input, tool_use_id: id })
const result = (id: string, name = 'run_code', res: Record<string, unknown> = {}): SSEEvent =>
  ({ type: 'tool_result', name, result: res, tool_use_id: id })

describe('console feed store', () => {
  beforeEach(() => resetConsole())

  it('folds a tool call lifecycle into ONE updating row', () => {
    noteTurnEvent(start('t1', 'run_code', { site: 'siteA', code: 'x = 1' }))
    let rows = consoleRows()
    expect(rows).toHaveLength(1)
    expect(rows[0].live).toBe(true)
    expect(rows[0].verb).toBe('run_code')
    expect(rows[0].site).toBe('siteA')

    noteTurnEvent({ type: 'tool_progress', name: 'run_code', tool_use_id: 't1', message: 'phase 2' })
    noteTurnEvent({ type: 'tool_chunk', tool_use_id: 't1', stream: 'stdout', text: 'aa', bytes_total: 2048, elapsed_s: 1 })
    noteTurnEvent(result('t1', 'run_code', { status: 'ok' }))

    rows = consoleRows()
    expect(rows).toHaveLength(1)                     // armed: still ONE row
    expect(rows[0].live).toBe(false)
    expect(rows[0].updates).toBe(2)
    expect(rows[0].summary).toBe('phase 2')
    expect(rows[0].facts?.bytes).toBe(2048)
    expect(rows[0].facts?.status).toBe('ok')
    expect(rows[0].facts?.dur_ms).toBeGreaterThanOrEqual(0)
  })

  it('marks a failed tool result as error severity', () => {
    noteTurnEvent(start('t2'))
    noteTurnEvent(result('t2', 'run_code', { status: 'error' }))
    expect(consoleRows()[0].severity).toBe('error')
  })

  it('shows an orphan tool_result (reattach) as its own row', () => {
    noteTurnEvent(result('never-started', 'fetch_data', { status: 'ok' }))
    const rows = consoleRows()
    expect(rows).toHaveLength(1)
    expect(rows[0].verb).toBe('fetch_data')
    expect(rows[0].live).toBeUndefined()
  })

  it('drops chat deltas and usage accounting', () => {
    noteTurnEvent({ type: 'delta', text: 'hello' })
    noteTurnEvent({ type: 'usage', input: 1, output: 2, cache_read: 0, cache_write: 0 })
    expect(consoleRows()).toHaveLength(0)
  })

  it('caps the ring at 2000, evicting oldest and keeping newest', () => {
    for (let i = 0; i < 2100; i++)
      noteTurnEvent({ type: 'notice', text: `n${i}` })
    const rows = consoleRows()
    expect(rows).toHaveLength(2000)
    expect(rows[0].summary).toBe('n100')             // oldest 100 evicted
    expect(rows[rows.length - 1].summary).toBe('n2099')
  })

  it('maps a backend console envelope through verbatim', () => {
    noteNotification({
      type: 'console', category: 'data', verb: 'chunk backhaul', site: 'siteB',
      severity: 'info', summary: 'c/0/0', bytes: 4096, dur_ms: 380, status: 'ok',
    } as NotificationEvent)
    const r = consoleRows()[0]
    expect(r.category).toBe('data')
    expect(r.site).toBe('siteB')
    expect(r.facts).toMatchObject({ bytes: 4096, dur_ms: 380, status: 'ok' })
  })

  it('clamps unknown categories/severities from the wire to safe values', () => {
    noteNotification({ type: 'console', category: 'weird', verb: 'x',
                       severity: 'catastrophic' } as unknown as NotificationEvent)
    const r = consoleRows()[0]
    expect(r.category).toBe('system')
    expect(r.severity).toBe('info')
  })

  it('skips hello and the legacy compute envelope (console doubles it)', () => {
    noteNotification({ type: 'hello' } as NotificationEvent)
    noteNotification({ type: 'compute', site: 's', phase: 'site.registered' } as NotificationEvent)
    expect(consoleRows()).toHaveLength(0)
  })

  it('notifies subscribers on push', () => {
    let called = 0
    const un = subscribeConsole(() => { called++ })
    noteTurnEvent({ type: 'notice', text: 'x' })
    expect(called).toBe(1)
    un()
    noteTurnEvent({ type: 'notice', text: 'y' })
    expect(called).toBe(1)
  })
})

describe('facet filtering', () => {
  beforeEach(() => {
    resetConsole()
    noteNotification({ type: 'console', category: 'data', verb: 'transfer.done',
                       site: 'siteA', severity: 'info' } as NotificationEvent)
    noteNotification({ type: 'console', category: 'run', verb: 'job.failed',
                       site: 'siteB', severity: 'error', status: 'exit 1' } as NotificationEvent)
    noteTurnEvent({ type: 'notice', text: 'model busy' })   // agent/warn, no site
  })

  const all = () => consoleRows()
  const none = { cats: null, sites: null, errorsOnly: false, q: '' }

  it('passes everything with no facets active', () => {
    expect(filterRows(all(), none)).toHaveLength(3)
  })

  it('filters by category set', () => {
    const out = filterRows(all(), { ...none, cats: new Set(['data' as const]) })
    expect(out).toHaveLength(1)
    expect(out[0].verb).toBe('transfer.done')
  })

  it('filters by site, with "" matching site-less rows', () => {
    expect(filterRows(all(), { ...none, sites: new Set(['siteB']) })).toHaveLength(1)
    const local = filterRows(all(), { ...none, sites: new Set(['']) })
    expect(local).toHaveLength(1)
    expect(local[0].verb).toBe('notice')
  })

  it('errorsOnly keeps warn AND error, drops info', () => {
    const out = filterRows(all(), { ...none, errorsOnly: true })
    expect(out.map(r => r.verb).sort()).toEqual(['job.failed', 'notice'])
  })

  it('free-text search matches verb, summary, site and status', () => {
    expect(filterRows(all(), { ...none, q: 'transfer' })).toHaveLength(1)
    expect(filterRows(all(), { ...none, q: 'siteb' })).toHaveLength(1)
    expect(filterRows(all(), { ...none, q: 'exit 1' })).toHaveLength(1)
    expect(filterRows(all(), { ...none, q: 'zzz' })).toHaveLength(0)
  })

  it('lists distinct sites for the facet chips', () => {
    expect(consoleSites(all())).toEqual(['siteA', 'siteB'])
  })
})
