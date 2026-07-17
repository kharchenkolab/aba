/**
 * Run-card action band: Discuss is the ONE primary verb and SEEDS the composer
 * (onPrefill — no auto-send, no focus yank); the rare re-run verbs live behind
 * the ⋯ overflow and seed too. onAsk remains only as the legacy fallback when
 * no prefill hook is wired.
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

  it('Discuss seeds the composer via onPrefill (never auto-sends via onAsk)', async () => {
    const onPrefill = vi.fn(); const onAsk = vi.fn()
    await act(async () => {
      render(<RunView run={doneRun} entities={[]} onFocus={() => {}} onChange={() => {}}
                      onAsk={onAsk} onPrefill={onPrefill} />)
    })
    fireEvent.click(screen.getByText('Discuss'))
    expect(onPrefill).toHaveBeenCalledTimes(1)
    expect(onPrefill.mock.calls[0][0]).toContain('entity_id="ana_x"')
    expect(onAsk).not.toHaveBeenCalled()
  })

  it('re-run verbs are in the ⋯ overflow, not the band, and seed the composer', async () => {
    const onPrefill = vi.fn()
    await act(async () => {
      render(<RunView run={doneRun} entities={[]} onFocus={() => {}} onChange={() => {}}
                      onPrefill={onPrefill} />)
    })
    expect(screen.queryByText('Re-run as-is')).toBeNull()          // hidden until ⋯
    fireEvent.click(screen.getByLabelText('More actions'))
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
    fireEvent.click(screen.getByText('Discuss'))
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
