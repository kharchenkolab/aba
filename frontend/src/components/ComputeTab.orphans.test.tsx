/**
 * "Deleted projects are still holding disk" — the manual reclaim affordance
 * (backend: core/compute/reclaim.py orphans, GET/POST /api/compute/orphans,
 * backend/tests/test_orphan_reclaim.py).
 *
 * The panel exists to be honest about substrate nobody can reach by name any
 * more. Two ceilings matter as much as the offer itself: nothing to reclaim
 * must render NOTHING (no zero-row noise), and a registry the server could not
 * read must render nothing rather than "0 GB" — a zero there would read as
 * "checked, all clean" when nothing was checked at all.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import ComputeTab from './ComputeTab'

function installFetch(orphans: unknown, swept?: unknown) {
  const calls: string[] = []
  // api.ts's _do reads r.text() and JSON.parses it — a mock that only
  // provides json() silently returns undefined for every call.
  const reply = (payload: unknown) => Promise.resolve({
    ok: true,
    text: () => Promise.resolve(JSON.stringify(payload)),
    json: () => Promise.resolve(payload),
  })
  globalThis.fetch = vi.fn().mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push(`${method} ${url}`)
      if (url.includes('/api/compute/orphans')) {
        return reply(method === 'POST' ? swept : orphans)
      }
      if (url.includes('/api/compute/status')) {
        return reply({ ok: true, detail: '', self_service: true })
      }
      if (url.includes('/api/compute/sites')) return reply({ sites: [] })
      if (url.includes('/api/compute/advanced')) return reply({ available: false })
      return reply(null)
    }) as unknown as typeof fetch
  return calls
}

const HELD = {
  orphans: [{ project: 'prj_gone_a' }, { project: 'prj_gone_b' }],
  reclaimable_bytes: 3_200_000_000,
}

describe('deleted-project reclaim', () => {
  beforeEach(() => { vi.restoreAllMocks() })
  afterEach(() => { vi.restoreAllMocks() })

  it('offers the reclaim and says what it will not touch', async () => {
    installFetch(HELD)
    await act(async () => { render(<ComputeTab />) })
    const body = document.body.textContent ?? ''
    expect(body).toMatch(/2 deleted projects still hold/)
    expect(body).toMatch(/3\.2 GB/)
    expect(body).toMatch(/never touched/)
    expect(screen.getByText('Reclaim now')).toBeTruthy()
  })

  it('sweeps only on an explicit click, and reports what came back', async () => {
    const calls = installFetch(HELD, { orphans: [{ project: 'prj_gone_a' }],
                                       freed_bytes: 3_200_000_000 })
    await act(async () => { render(<ComputeTab />) })
    expect(calls.filter(c => c.startsWith('POST /api/compute/orphans'))).toHaveLength(0)
    await act(async () => { fireEvent.click(screen.getByText('Reclaim now')) })
    expect(calls.filter(c => c.startsWith('POST /api/compute/orphans'))).toHaveLength(1)
    expect(document.body.textContent ?? '').toMatch(/Reclaimed 3\.2 GB from 1 deleted project/)
  })

  it('renders nothing when there is nothing to reclaim', async () => {
    installFetch({ orphans: [], reclaimable_bytes: 0 })
    await act(async () => { render(<ComputeTab />) })
    expect(screen.queryByText('Reclaim now')).toBeNull()
    expect(document.body.textContent ?? '').not.toMatch(/deleted project/)
  })

  it('says so when the registry could not be read, and offers no sweep', async () => {
    // Silence here is indistinguishable from "checked, all clean" — and
    // nothing was checked. A zero would be worse still.
    installFetch({ refused: 'the project registry could not be read',
                   orphans: [], reclaimable_bytes: 0 })
    await act(async () => { render(<ComputeTab />) })
    const body = document.body.textContent ?? ''
    expect(body).toMatch(/could not be assessed/)
    expect(body).toMatch(/registry could not be read/)
    expect(body).not.toMatch(/0 GB/)
    expect(screen.queryByText('Reclaim now')).toBeNull()
  })
})
