/**
 * Console component — rendering contract over the feed store: dense rows
 * (glyph + site chip + verb + facts, NO time column), gap dividers on >60s
 * silences, click-to-expand detail, facet chips filtering live, follow-tail
 * "N new" pill logic exercised via the store.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Console from './Console'
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
