/**
 * Console component — rendering contract over the feed store: dense rows
 * (glyph + site chip + verb + facts, NO time column), gap dividers on >60s
 * silences, click-to-expand detail, facet chips filtering live, follow-tail
 * "N new" pill logic exercised via the store.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Console, { durClass } from './Console'
import { noteNotification, noteTurnEvent, resetConsole } from '../console'
import type { NotificationEvent, SSEEvent } from '../wire'

const backhaul = (over: Record<string, unknown> = {}): NotificationEvent => ({
  type: 'console', category: 'data', verb: 'chunk backhaul', site: 'siteA',
  severity: 'info', summary: 'c/0/0', bytes: 4096, dur_ms: 380, status: 'ok',
  ...over,
} as NotificationEvent)

describe('Console rendering', () => {
  // resetConsole() clears the feed store, but the facet chips PERSIST to
  // localStorage (Console.tsx loadFacets/saveFacets) and are read back on
  // mount — so the errors-only toggle exercised below stayed on for every
  // later test in this file, filtering their rows away and failing them on
  // suite order alone. Clear both: the store AND the persisted facets.
  // …and guard the clear itself: `localStorage` is NOT present in every test
  // environment (happy-dom here does not expose it), so an unguarded
  // localStorage.clear() throws in beforeEach and fails all seven tests before
  // they run. Console.tsx already treats storage as optional (loadFacets /
  // saveFacets swallow); the cleanup has to be just as tolerant.
  beforeEach(() => {
    resetConsole()
    try { globalThis.localStorage?.clear() } catch { /* no storage here */ }
  })

  it('renders a dense row: glyph + site chip + verb + facts, no time column', () => {
    noteNotification(backhaul())
    const { container } = render(<Console />)
    const chip = container.querySelector('.crow__site')      // colored site chip
    expect(chip?.textContent).toBe('siteA')
    expect(screen.getByText('chunk backhaul')).toBeTruthy() // verb
    const facts = screen.getByText(/4\.0KB/)                // trailing fact cluster
    expect(facts.textContent).toContain('380ms')
    // no per-row clock — time only in the hover title
    const line = screen.getByText('chunk backhaul').closest('.crow__line')!
    expect(line.getAttribute('title')).toBeTruthy()
    expect(line.textContent).not.toMatch(/\d{2}:\d{2}:\d{2}/)
  })

  it('expands a row to its structured detail on click', () => {
    noteNotification(backhaul({ detail: { via: 'batch', probe: 7 } }))
    render(<Console />)
    fireEvent.click(screen.getByText('chunk backhaul'))
    expect(screen.getByText(/"via": "batch"/)).toBeTruthy()
    expect(screen.getByText('copy')).toBeTruthy()
  })

  it('filters by category chip and errors-only toggle', () => {
    noteNotification(backhaul())
    noteNotification({ type: 'console', category: 'run', verb: 'job.failed',
                       severity: 'error', site: 'siteB' } as NotificationEvent)
    render(<Console />)
    expect(screen.getByText('chunk backhaul')).toBeTruthy()
    fireEvent.click(screen.getByTitle('filter: run'))          // category facet
    expect(screen.queryByText('chunk backhaul')).toBeNull()
    expect(screen.getByText('job.failed')).toBeTruthy()
    fireEvent.click(screen.getByTitle('filter: run'))          // back to all
    fireEvent.click(screen.getByTitle('errors + warnings only'))
    expect(screen.queryByText('chunk backhaul')).toBeNull()
    expect(screen.getByText('job.failed')).toBeTruthy()
  })

  it('filters by free-text search', () => {
    noteNotification(backhaul())
    noteNotification(backhaul({ verb: 'transfer.done', site: 'siteB' }))
    render(<Console />)
    fireEvent.change(screen.getByPlaceholderText('filter…'), { target: { value: 'transfer' } })
    expect(screen.queryByText('chunk backhaul')).toBeNull()
    expect(screen.getByText('transfer.done')).toBeTruthy()
  })

  it('shows a live pulsing row for an open tool call, closing on result', () => {
    noteTurnEvent({ type: 'tool_start', name: 'run_code', input: { site: 'siteA' },
                    tool_use_id: 't1' } as SSEEvent)
    const { container, rerender } = render(<Console />)
    expect(container.querySelector('.crow--live')).toBeTruthy()
    noteTurnEvent({ type: 'tool_result', name: 'run_code', result: { status: 'ok' },
                    tool_use_id: 't1' } as SSEEvent)
    rerender(<Console />)
    expect(container.querySelector('.crow--live')).toBeNull()
    expect(container.querySelectorAll('.crow')).toHaveLength(1)  // one row, updated in place
  })

  it('inserts a gap divider only across >60s silences', () => {
    noteNotification(backhaul())
    noteNotification(backhaul({ verb: 'second' }))
    const { container } = render(<Console />)
    expect(container.querySelector('.console__gap')).toBeNull() // no fake gaps
  })

  it('says which empty it is: no events vs no matches', () => {
    const { rerender } = render(<Console />)
    expect(screen.getByText(/No activity yet/)).toBeTruthy()
    noteNotification(backhaul())
    rerender(<Console />)
    fireEvent.change(screen.getByPlaceholderText('filter…'), { target: { value: 'zzz' } })
    expect(screen.getByText(/Nothing matches/)).toBeTruthy()
  })
})

describe('duration emphasis', () => {
  // Own reset: the feed store is a module singleton, so without this the rows
  // from the block above leak in and `querySelector('.crow__dur')` picks THEIR
  // duration instead of this test's (the same cross-test leak class as the
  // persisted facets — it bit this very test first time out).
  beforeEach(() => {
    resetConsole()
    try { globalThis.localStorage?.clear() } catch { /* no storage here */ }
  })

  it('never uses an error tier — red is reserved for severity', () => {
    // A slow step is not a broken step. Duration used the SAME red as
    // crow--error, so a 12s kernel start (normal work) read as a failure.
    for (const ms of [0, 999, 5_000, 9_999, 10_000, 59_999, 60_000, 600_000]) {
      expect(durClass(ms)).not.toMatch(/err|error|fail/)
    }
  })

  it('stays quiet under 10s and escalates in two amber tiers', () => {
    // Thresholds are deliberately high: at 2s virtually every remote step lit
    // up, which trains the eye to ignore the signal.
    expect(durClass(1_500)).toBe('')
    expect(durClass(9_999)).toBe('')
    expect(durClass(10_000)).toBe('is-slow1')
    expect(durClass(59_999)).toBe('is-slow1')
    expect(durClass(60_000)).toBe('is-slow2')
  })

  it('a slow but SUCCESSFUL row is not styled as an error', () => {
    noteNotification({ type: 'console', category: 'run', verb: 'kernel.started',
                       site: 'siteA', severity: 'info', status: 'ok',
                       dur_ms: 42_000 } as NotificationEvent)
    const { container } = render(<Console />)
    expect(container.querySelector('.crow--error')).toBeNull()
    const dur = container.querySelector('.crow__dur')!
    expect(dur.className).toContain('is-slow1')
    expect(dur.textContent).toContain('42.0s')
  })
})
