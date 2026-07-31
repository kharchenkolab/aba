/**
 * Delete-confirm flow — UX half of the hard-delete contract
 * (backend: entities_delete, tests/test_delete_blockers.py).
 *
 * A 409 refusal must be ACTIONABLE, not a dead end: it lists the
 * dependents and offers the real levers — "Archive instead" (soft,
 * always succeeds) and, when the server advertises can_override,
 * "Delete anyway" (retry with ?force=true). A server that does NOT
 * advertise the override gets no "Delete anyway" button (ceiling:
 * the UI must not invent capabilities).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import EntityMenu from './EntityMenu'
import type { Entity } from '../types'

const fig = {
  id: 'fig_1', type: 'figure', title: 'Lone figure', status: 'active',
  tags: [], metadata: {},
} as unknown as Entity

const result = {
  id: 'res_9', type: 'result', title: 'A result', status: 'active',
  tags: [], metadata: { members: [] },
} as unknown as Entity

function installFetch(calls: string[], opts: { canOverride?: boolean } = {}) {
  globalThis.fetch = vi.fn().mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push(`${method} ${url}`)
      if (method === 'DELETE' && url.includes('hard=true') && !url.includes('force=true')) {
        return Promise.resolve({
          ok: false, status: 409,
          json: () => Promise.resolve({ detail: {
            error: '1 live entity depends on this one.',
            references: [{ id: 'res_1', type: 'result', title: 'Result A', rel_type: 'includes' }],
            ...(opts.canOverride === false ? {} : { can_override: true }),
          } }),
          text: () => Promise.resolve(''),
        })
      }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ ok: true }),
        text: () => Promise.resolve(''),
      })
    }) as unknown as typeof globalThis.fetch
}

async function openDeleteAndConfirm(entity: Entity, onChange: () => void) {
  await act(async () => {
    render(<EntityMenu entity={entity} onChange={onChange} />)
  })
  fireEvent.click(screen.getByTitle('More actions'))
  fireEvent.click(screen.getByText('Delete…'))
  await act(async () => { fireEvent.click(screen.getByText('Delete')) })
}

describe('EntityMenu delete flow', () => {
  let origFetch: typeof globalThis.fetch
  let calls: string[]
  beforeEach(() => { origFetch = globalThis.fetch; calls = [] })
  afterEach(() => { globalThis.fetch = origFetch; vi.restoreAllMocks() })

  it('409 shows the dependents and offers Archive instead + Delete anyway', async () => {
    installFetch(calls)
    await openDeleteAndConfirm(fig, vi.fn())
    // armed: the hard delete actually fired, with the figure-shaped query
    expect(calls).toContain('DELETE /api/entities/fig_1?hard=true')
    expect(screen.getByText(/Result A/)).toBeTruthy()       // dependent named
    expect(screen.getByText('Archive instead')).toBeTruthy()
    expect(screen.getByText('Delete anyway')).toBeTruthy()
  })

  it('Archive instead soft-deletes (no ?hard) and refreshes', async () => {
    installFetch(calls)
    const onChange = vi.fn()
    await openDeleteAndConfirm(fig, onChange)
    await act(async () => { fireEvent.click(screen.getByText('Archive instead')) })
    expect(calls).toContain('DELETE /api/entities/fig_1')
    expect(onChange).toHaveBeenCalled()
  })

  it('Delete anyway retries with &force=true and refreshes', async () => {
    installFetch(calls)
    const onChange = vi.fn()
    await openDeleteAndConfirm(fig, onChange)
    await act(async () => { fireEvent.click(screen.getByText('Delete anyway')) })
    expect(calls).toContain('DELETE /api/entities/fig_1?hard=true&force=true')
    expect(onChange).toHaveBeenCalled()
  })

  it('no can_override in the 409 → no Delete anyway (Archive still offered)', async () => {
    installFetch(calls, { canOverride: false })
    await openDeleteAndConfirm(fig, vi.fn())
    expect(screen.getByText(/Result A/)).toBeTruthy()
    expect(screen.getByText('Archive instead')).toBeTruthy()
    expect(screen.queryByText('Delete anyway')).toBeNull()
  })

  it('a non-409 failure still offers a retry, not just Archive', async () => {
    // A transient 500 sets the same error state as a 409 but with no
    // can_override, so keying the primary button on `error` left the user
    // with Cancel + Archive and no way to retry the delete they asked for.
    globalThis.fetch = vi.fn().mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        const method = init?.method ?? 'GET'
        calls.push(`${method} ${url}`)
        if (method === 'DELETE' && url.includes('hard=true')) {
          return Promise.resolve({
            ok: false, status: 500,
            json: () => Promise.reject(new Error('not json')),
            text: () => Promise.resolve('boom'),
          })
        }
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ ok: true }),
          text: () => Promise.resolve(''),
        })
      }) as unknown as typeof globalThis.fetch
    await openDeleteAndConfirm(fig, vi.fn())
    expect(screen.queryByText('Delete anyway')).toBeNull()   // no override offered
    const retry = screen.getByText('Delete')                 // the retry survives
    const before = calls.length
    await act(async () => { fireEvent.click(retry) })
    expect(calls.length).toBeGreaterThan(before)             // armed: it re-fired
  })

  it('result delete keeps the cascade=members query (traits unchanged)', async () => {
    installFetch(calls)
    await openDeleteAndConfirm(result, vi.fn())
    expect(calls).toContain('DELETE /api/entities/res_9?hard=true&cascade=members')
  })
})
