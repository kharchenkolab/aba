/**
 * The durable-uncheck consequence preview (backend: core/data/ledger.py
 * site_holdings, tests/test_data_ledger.py).
 *
 * The card claims N kept results "would become at risk". That is true only of
 * the keeps whose bytes are STILL on the machine: a keep that was shipped to
 * the workspace still names this site as its ORIGIN, and counting those rows
 * overstated the consequence of a reversible settings change.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import ComputeTab from './ComputeTab'

function installFetch(holdings: unknown) {
  const reply = (payload: unknown) => Promise.resolve({
    ok: true,
    text: () => Promise.resolve(JSON.stringify(payload)),
    json: () => Promise.resolve(payload),
  })
  globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/holdings')) return reply(holdings)
    if (url.includes('/api/compute/status'))
      return reply({ ok: true, detail: '', self_service: true })
    if (url.includes('/api/compute/sites'))
      return reply({ sites: [{ name: 'siteA', kind: 'ssh',
                               config: { root: '/scratch/x', durable: true },
                               // the facts block (which carries the durable
                               // checkbox) renders only for a probed site
                               capabilities: { cores: 8, internet: true,
                                               scheduler: { type: 'none' } } }] })
    if (url.includes('/api/compute/advanced')) return reply({ available: false })
    if (url.includes('/api/compute/orphans')) return reply({ orphans: [] })
    return reply(null)
  }) as unknown as typeof fetch
}

async function uncheckDurable(holdings: unknown) {
  installFetch(holdings)
  await act(async () => { render(<ComputeTab />) })
  // the card is collapsed until its head is clicked
  await act(async () => { fireEvent.click(screen.getByText('siteA')) })
  const label = screen.getByText('durable storage').closest('label')!
  await act(async () => {
    fireEvent.click(label.querySelector('input[type=checkbox]')!)
  })
}

describe('durable-uncheck preview', () => {
  let origFetch: typeof globalThis.fetch
  beforeEach(() => { origFetch = globalThis.fetch })
  afterEach(() => { globalThis.fetch = origFetch; vi.restoreAllMocks() })

  it('counts only the keeps whose bytes are still on the machine', async () => {
    // two kept runs on siteA; one was already shipped to the workspace
    await uncheckDurable({ site: 'siteA', kept_runs: 2, kept_bytes: 100e9,
                           kept_in_place: { runs: 1, bytes: 40e9 },
                           dataset_homes: [], at_risk_if_gone: 2 })
    expect(screen.getByText(/1 kept result /)).toBeTruthy()
    expect(screen.getByText(/40 GB/)).toBeTruthy()
    expect(screen.queryByText(/2 kept results/)).toBeNull()
  })

  it('ARMED: it still warns when the keeps really are all in place', async () => {
    await uncheckDurable({ site: 'siteA', kept_runs: 2, kept_bytes: 100e9,
                           kept_in_place: { runs: 2, bytes: 100e9 },
                           dataset_homes: [], at_risk_if_gone: 2 })
    expect(screen.getByText(/2 kept results/)).toBeTruthy()
  })

  it('an outage still confirms — zeros must not read as "nothing here"', async () => {
    await uncheckDurable({ site: 'siteA', kept_runs: 0, kept_bytes: 0,
                           kept_in_place: { runs: 0, bytes: 0 },
                           dataset_homes: [], at_risk_if_gone: 0,
                           unknown: true, note: 'compute substrate unreachable' })
    expect(screen.getAllByText(/substrate unreachable/).length).toBeGreaterThan(0)
  })
})
