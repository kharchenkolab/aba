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
      why: 'referenced in place on siteC, which declares no durable storage' },
    { entity_id: 'ds2', kind: 'dataset', title: 'reference set', state: 'safe', site: 'siteB', why: 'durable home' },
  ],
  totals: { items: 2, safe: 1, at_risk: 1, changed: 0, unknown: 0 },
  remote_sites: ['siteB', 'siteC'], multi_site: true,
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

  it('renders the verdict + Review list when something needs attention', async () => {
    mockLedger(noisyLedger)
    const onFocus = vi.fn()
    await act(async () => { render(<LedgerStrip projectId="p1" onFocus={onFocus} />) })
    expect(screen.getByText(/2 items · 1 safe/)).toBeTruthy()
    expect(screen.getByText('1 at risk')).toBeTruthy()
    fireEvent.click(screen.getByText('Review'))
    expect(screen.getByText(/declares no durable storage/)).toBeTruthy()
    fireEvent.click(screen.getByText('shared table'))
    expect(onFocus).toHaveBeenCalledWith('ds1')
  })

  it('multi-site but all-safe: one quiet line, no flags, no Review', async () => {
    mockLedger({ ...noisyLedger,
      items: noisyLedger.items.map(i => ({ ...i, state: 'safe' })),
      totals: { items: 2, safe: 2, at_risk: 0, changed: 0, unknown: 0 } })
    await act(async () => { render(<LedgerStrip projectId="p1" />) })
    expect(screen.getByText(/2 items · 2 safe/)).toBeTruthy()
    expect(screen.queryByText('Review')).toBeNull()
    expect(screen.queryByText(/at risk/)).toBeNull()
  })
})
