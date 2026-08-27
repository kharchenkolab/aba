/**
 * §1 ledger strip + the LOCAL-ONLY SNAPSHOT CONTRACT (more_weft_ui.md):
 * a project whose items are all safe and all local must render ZERO ledger
 * chrome — the strip is the construct, absence is the default. Any PR that
 * breaks the quiet case is adding confusion, whatever else it adds.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import LedgerStrip, { type Ledger } from './LedgerStrip'

function mockLedger(led: Ledger) {
  globalThis.fetch = vi.fn().mockImplementation(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(led) }),
  ) as unknown as typeof globalThis.fetch
}

const quietLedger: Ledger = {
  items: [
    { entity_id: 'ds1', kind: 'dataset', title: 'inputs', state: 'safe', site: null, why: 'managed in the workspace' },
    { entity_id: 'run1', kind: 'run_keeps', state: 'safe', site: 'local', why: 'kept on durable storage' },
  ],
  totals: { items: 2, safe: 2, at_risk: 0, changed: 0, unknown: 0 },
  remote_sites: [], multi_site: false,
}

const noisyLedger: Ledger = {
  items: [
    { entity_id: 'ds1', kind: 'dataset', title: 'shared table', state: 'at_risk', site: 'siteC',
      linkable: true,
      why: 'referenced in place on siteC, which declares no durable storage' },
    { entity_id: 'ds2', kind: 'dataset', title: 'reference set', state: 'safe', site: 'siteB',
      linkable: true, why: 'durable home' },
  ],
  totals: { items: 2, safe: 1, at_risk: 1, changed: 0, unknown: 0 },
  remote_sites: ['siteB', 'siteC'], multi_site: true,
}

const degradedLedger: Ledger = {
  items: [], totals: { items: 0, safe: 0, at_risk: 0, changed: 0, unknown: 0 },
  remote_sites: [], multi_site: false,
  degraded: true,
  degraded_note: 'the retention index is unreachable — the safety of kept results cannot be assessed right now (they are missing from this list)',
}

describe('LedgerStrip', () => {
  let origFetch: typeof globalThis.fetch
  beforeEach(() => { origFetch = globalThis.fetch })
  afterEach(() => { globalThis.fetch = origFetch; vi.restoreAllMocks() })

  it('LOCAL-ONLY SNAPSHOT: all-safe-and-local renders NOTHING', async () => {
    mockLedger(quietLedger)
    let container: HTMLElement
    await act(async () => { ({ container } = render(<LedgerStrip projectId="p1" />)) })
    expect(container!.innerHTML).toBe('')          // zero chrome, not a green banner
    expect(screen.queryByText(/safe/)).toBeNull()
    expect(screen.queryByText(/site/i)).toBeNull()
  })

  it('REFETCHES when the fingerprint changes (stale-quiet regression)', async () => {
    // a mid-session registration flipped the DATA non-quiet, but the strip
    // fetched once on mount and stayed silently stale until a full reload
    mockLedger(quietLedger)
    let rr: ReturnType<typeof render>
    await act(async () => { rr = render(<LedgerStrip projectId="p1" fingerprint="1:a" />) })
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    mockLedger(noisyLedger)   // fresh mock — the refetch is its 1st call
    await act(async () => { rr!.rerender(<LedgerStrip projectId="p1" fingerprint="2:b" />) })
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/at risk/)).toBeTruthy()
  })

  it('DEGRADED is never quiet: an outage renders the warning, not silence', async () => {
    // quiet means "all safe" — during a substrate outage the kept rows are
    // MISSING from the ledger, so silence would claim safety we can't assess
    mockLedger(degradedLedger)
    await act(async () => { render(<LedgerStrip projectId="p1" />) })
    expect(screen.getByText(/retention index is unreachable/)).toBeTruthy()
  })

  it('renders the attention verdict + Review list when something needs attention', async () => {
    mockLedger(noisyLedger)
    const onFocus = vi.fn()
    await act(async () => { render(<LedgerStrip projectId="p1" onFocus={onFocus} />) })
    // leads with the PROBLEM, not the census
    expect(screen.getByText(/1 of 2 items needs attention/)).toBeTruthy()
    expect(screen.getByText('1 at risk')).toBeTruthy()
    fireEvent.click(screen.getByText('Review'))
    expect(screen.getByText(/declares no durable storage/)).toBeTruthy()
    fireEvent.click(screen.getByText('shared table'))
    expect(onFocus).toHaveBeenCalledWith('ds1')
  })

  it('multi-site but all-safe renders NOTHING (where data lives is card business)', async () => {
    // "86 items · 86 safe (some on …)" was full-width plaintext chrome that
    // answered a question nobody asked (live UX finding, 2026-07-25): the
    // strip flags ATTENTION; an item's home site is shown on its own card.
    mockLedger({ ...noisyLedger,
      items: noisyLedger.items.map(i => ({ ...i, state: 'safe' })),
      totals: { items: 2, safe: 2, at_risk: 0, changed: 0, unknown: 0 } })
    let container: HTMLElement
    await act(async () => { ({ container } = render(<LedgerStrip projectId="p1" />)) })
    expect(container!.innerHTML).toBe('')
    expect(screen.queryByText(/safe/)).toBeNull()
    expect(screen.queryByText(/siteB|siteC/)).toBeNull()
  })
})

describe('LedgerStrip — naming, linking, repair', () => {
  let origFetch: typeof globalThis.fetch
  beforeEach(() => { origFetch = globalThis.fetch })
  afterEach(() => { globalThis.fetch = origFetch; vi.restoreAllMocks() })

  /** a kept RUN flagged at risk — the live shape (2026-08-27) that rendered
   *  as a bare id with a dead button and no way to act on it */
  const keepLedger: Ledger = {
    items: [
      { entity_id: 'ana_1', kind: 'run_keeps', title: 'clustering pass', state: 'at_risk',
        site: 'local/siteA', linkable: true,
        why: 'kept in place on siteA, which no longer declares durable storage',
        remedy: { action: 'ship_home', label: 'Copy to the workspace',
                  targets: ['jb_1'], note: 'copies these files off siteA' } },
    ],
    totals: { items: 1, safe: 0, at_risk: 1, changed: 0, unknown: 0 },
    remote_sites: ['siteA'], multi_site: true,
  }

  it('a flagged RUN is named and focusable, not a dead id', async () => {
    // it rendered `ana_a89bd4a1 — at risk: …` with the button hard-disabled
    // for every kind but `dataset`, so the one thing needing attention was
    // the one thing you could not open
    mockLedger(keepLedger)
    const onFocus = vi.fn()
    await act(async () => { render(<LedgerStrip projectId="p1" onFocus={onFocus} />) })
    fireEvent.click(screen.getByText('Review'))
    fireEvent.click(screen.getByText('clustering pass'))
    expect(onFocus).toHaveBeenCalledWith('ana_1')
  })

  it('an unattributable item stays unclickable (the button must not lie)', async () => {
    mockLedger({ ...keepLedger,
      items: [{ ...keepLedger.items[0], linkable: false, title: null }] })
    const onFocus = vi.fn()
    await act(async () => { render(<LedgerStrip projectId="p1" onFocus={onFocus} />) })
    fireEvent.click(screen.getByText('Review'))
    fireEvent.click(screen.getByText('ana_1'))
    expect(onFocus).not.toHaveBeenCalled()
  })

  it('the repair hands the Guide a prefilled ask — it moves no bytes itself', async () => {
    mockLedger(keepLedger)
    const onPrefill = vi.fn()
    await act(async () => { render(<LedgerStrip projectId="p1" onPrefill={onPrefill} />) })
    fireEvent.click(screen.getByText('Review'))
    fireEvent.click(screen.getByText('Ask the Guide to fix this'))
    expect(onPrefill).toHaveBeenCalledTimes(1)
    const msg = onPrefill.mock.calls[0][0] as string
    // everything the agent needs to act without asking a follow-up question
    expect(msg).toContain('ana_1')
    expect(msg).toContain('clustering pass')
    expect(msg).toContain('no longer declares durable storage')
    // and no request was made from the strip itself
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)   // the ledger read only
  })

  it('offers no repair where none exists (a safe item has no button)', async () => {
    mockLedger({ ...keepLedger,
      items: [{ ...keepLedger.items[0], state: 'changed', remedy: undefined,
                why: 'the data at its source changed since registration' }],
      totals: { items: 1, safe: 0, at_risk: 0, changed: 1, unknown: 0 } })
    await act(async () => { render(<LedgerStrip projectId="p1" onPrefill={vi.fn()} />) })
    fireEvent.click(screen.getByText('Review'))
    expect(screen.queryByText('Ask the Guide to fix this')).toBeNull()
  })

  it('an at-risk keep in ANOTHER project is counted, never silently dropped', async () => {
    // scoping the list to the project is right; going quiet about a result
    // at risk somewhere else is the outage bug wearing a different hat
    mockLedger({ items: [], totals: { items: 0, safe: 0, at_risk: 0, changed: 0, unknown: 0 },
                 remote_sites: [], multi_site: false,
                 elsewhere: { items: 12, at_risk: 2 } })
    await act(async () => { render(<LedgerStrip projectId="p1" />) })
    expect(screen.getByText(/2 kept results outside this project need attention/)).toBeTruthy()
  })

  it('QUIESCENCE HOLDS: elsewhere items that are all SAFE keep the strip silent', async () => {
    // the live case — 32 workspace keeps, none at risk. Counting them in the
    // strip would be the census chrome the quiet contract exists to prevent.
    mockLedger({ items: [], totals: { items: 0, safe: 0, at_risk: 0, changed: 0, unknown: 0 },
                 remote_sites: [], multi_site: false,
                 elsewhere: { items: 32, at_risk: 0 } })
    let container: HTMLElement
    await act(async () => { ({ container } = render(<LedgerStrip projectId="p1" />)) })
    expect(container!.innerHTML).toBe('')
  })
})
