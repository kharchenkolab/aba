/**
 * Run-card action band (§8b/§8d): NO run-level Discuss button — the focused
 * run's chat peek is the conversation (context-aware composer placeholder).
 * The band holds one verdict sentence + Cancel (active) + a quiet ⋯ overflow
 * (re-run verbs + Reproduce), all of which SEED the composer via onPrefill
 * (no auto-send); onAsk is only the legacy fallback.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import RunView from './RunView'

const doneRun = { id: 'ana_x', type: 'analysis', title: 'sweep', metadata: {
  run: { status: 'succeeded' } } } as unknown as never

function mockFetch() {
  globalThis.fetch = vi.fn().mockImplementation(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(null) }),
  ) as unknown as typeof globalThis.fetch
}

describe('RunView action band', () => {
  let origFetch: typeof globalThis.fetch
  beforeEach(() => { origFetch = globalThis.fetch; mockFetch() })
  afterEach(() => { globalThis.fetch = origFetch; vi.restoreAllMocks() })

  it('has NO run-level Discuss button (§8b: focus replaces Discuss)', async () => {
    await act(async () => {
      render(<RunView run={doneRun} entities={[]} onFocus={() => {}} onChange={() => {}}
                      onPrefill={() => {}} onAsk={() => {}} />)
    })
    expect(screen.queryByText('Discuss')).toBeNull()
  })

  it('renders the §8d verdict sentence for a quiet local run', async () => {
    await act(async () => {
      render(<RunView run={doneRun} entities={[]} onFocus={() => {}} onChange={() => {}} />)
    })
    // local single-site quiescence: "ran locally", no site talk, no safety word
    expect(screen.getByText(/^ran locally/)).toBeTruthy()
  })

  it('failed runs headline the cause in the verdict', async () => {
    const failed = { id: 'ana_f', type: 'analysis', title: 'sweep', metadata: {
      run: { status: 'failed', error: 'the input data at /x changed since registration\nlong trace…' },
    } } as unknown as never
    await act(async () => {
      render(<RunView run={failed} entities={[]} onFocus={() => {}} onChange={() => {}} />)
    })
    expect(screen.getByText(/^stopped: the input data at \/x changed/)).toBeTruthy()
  })

  it('re-run verbs + Reproduce live in the ⋯ overflow and seed the composer', async () => {
    const onPrefill = vi.fn()
    await act(async () => {
      render(<RunView run={doneRun} entities={[]} onFocus={() => {}} onChange={() => {}}
                      onPrefill={onPrefill} />)
    })
    expect(screen.queryByText('Re-run as-is')).toBeNull()          // hidden until ⋯
    fireEvent.click(screen.getByLabelText('More actions'))
    expect(screen.getByText('Reproduce')).toBeTruthy()
    fireEvent.click(screen.getByText('Re-run as-is'))
    expect(onPrefill).toHaveBeenCalledTimes(1)
    expect(onPrefill.mock.calls[0][0]).toMatch(/^Re-run "sweep" \(entity_id="ana_x"\) as-is\./)
    expect(screen.queryByText('Re-run with changes…')).toBeNull()  // menu closed after pick
  })

  it('falls back to onAsk when no prefill hook is wired (legacy hosts)', async () => {
    const onAsk = vi.fn()
    await act(async () => {
      render(<RunView run={doneRun} entities={[]} onFocus={() => {}} onChange={() => {}}
                      onAsk={onAsk} />)
    })
    fireEvent.click(screen.getByLabelText('More actions'))
    fireEvent.click(screen.getByText('Re-run as-is'))
    expect(onAsk).toHaveBeenCalledTimes(1)
  })

  it('active run: Cancel shown, ⋯ overflow hidden', async () => {
    const running = { id: 'ana_r', type: 'analysis', title: 'sweep', metadata: {
      run: { status: 'running' } } } as unknown as never
    await act(async () => {
      render(<RunView run={running} entities={[]} onFocus={() => {}} onChange={() => {}}
                      onPrefill={() => {}} />)
    })
    expect(screen.getByText('Cancel')).toBeTruthy()
    expect(screen.queryByLabelText('More actions')).toBeNull()
  })
})
