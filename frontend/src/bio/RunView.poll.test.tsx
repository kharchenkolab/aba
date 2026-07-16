/**
 * F3: the Files panel polls /api/runs/{id}/durable while any file is still settling
 * (pinned-pending), so the badge flips to kept live when weft captures at kernel stop,
 * then stops polling. output_durability.md §6.2.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import RunView, { treeHasPending, pruneCleared } from './RunView'
import type { TreeNode } from './FileBrowser'

describe('pruneCleared', () => {
  it('drops cleared file nodes, keeps the rest', () => {
    const t: TreeNode = { kind: 'root', name: '', path: '', children: [
      { kind: 'file', name: 'a', path: 'a', state: 'kept' },
      { kind: 'file', name: 'g', path: 'g', state: 'cleared' },
      { kind: 'folder', name: 'd', path: 'd', children: [
        { kind: 'file', name: 'x', path: 'd/x', state: 'cleared' },
        { kind: 'file', name: 'y', path: 'd/y', state: 'in-sandbox' }] },
    ] }
    const p = pruneCleared(t)
    const names = (n: TreeNode): string[] => n.kind === 'file' ? [n.name]
      : (n.children || []).flatMap(names)
    expect(names(p).sort()).toEqual(['a', 'y'])
  })
})

describe('treeHasPending', () => {
  it('is true iff some file is pinned-pending', () => {
    const t: TreeNode = {
      kind: 'root', name: '', path: '', children: [
        { kind: 'folder', name: 'd', path: 'd', children: [
          { kind: 'file', name: 'a', path: 'd/a', state: 'kept' },
          { kind: 'file', name: 'b', path: 'd/b', state: 'pinned-pending' },
        ] },
      ],
    }
    expect(treeHasPending(t)).toBe(true)
    expect(treeHasPending({ kind: 'root', name: '', path: '', children: [
      { kind: 'file', name: 'a', path: 'a', state: 'kept' }] })).toBe(false)
  })
})

const pendingTree = { kind: 'root', name: '', path: '', children: [
  { kind: 'file', name: 'big.h5ad', path: 'big.h5ad', state: 'pinned-pending', badge: 'pending' }] }
const keptTree = { kind: 'root', name: '', path: '', children: [
  { kind: 'file', name: 'big.h5ad', path: 'big.h5ad', state: 'kept', badge: 'kept ✓' }] }

describe('RunView durability polling', () => {
  let origFetch: typeof globalThis.fetch
  beforeEach(() => { origFetch = globalThis.fetch; vi.useFakeTimers() })
  afterEach(() => { globalThis.fetch = origFetch; vi.useRealTimers(); vi.restoreAllMocks() })

  it('polls /durable while pinned-pending, flips to kept, then stops', async () => {
    let calls = 0
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/durable')) {
        calls++
        return Promise.resolve({ ok: true, json: () => Promise.resolve(calls === 1 ? pendingTree : keptTree) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }) as unknown as typeof globalThis.fetch

    const run = { id: 'ana_1', type: 'analysis', title: 'R', metadata: {} } as unknown as never
    await act(async () => {
      render(<RunView run={run} entities={[]} onFocus={() => {}} onChange={() => {}} />)
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })   // flush initial fetch
    expect(screen.getByText('saving…')).toBeTruthy()
    expect(calls).toBe(1)

    // advance the poll → refetch → kept
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(screen.getByText('kept ✓')).toBeTruthy()
    expect(calls).toBe(2)

    // no more polling once nothing is pending
    await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
    expect(calls).toBe(2)
  })

  it('keeps polling while the Run is OPEN (picks up newly-harvested files), no pending needed', async () => {
    let calls = 0
    globalThis.fetch = vi.fn().mockImplementation((url: string) =>
      String(url).includes('/durable')
        ? (calls++, Promise.resolve({ ok: true, json: () => Promise.resolve(keptTree) }))
        : Promise.resolve({ ok: true, json: () => Promise.resolve({}) })) as unknown as typeof globalThis.fetch
    // run_state: 'open' → an in-progress run; the panel must refresh as artifacts land
    const run = { id: 'ana_open', type: 'analysis', title: 'R',
                  metadata: { run_state: 'open' } } as unknown as never
    await act(async () => {
      render(<RunView run={run} entities={[]} onFocus={() => {}} onChange={() => {}} />)
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(calls).toBe(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(calls).toBe(2)                 // polled again purely because the run is open
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(calls).toBe(3)
  })
})

const mixedTree = {
  kind: 'root', name: '', path: '',
  summary: { kept: 2, pinned_pending: 0, in_sandbox: 0, cleared: 1, total: 3 },
  children: [
    { kind: 'file', name: 'a.png', path: 'a.png', state: 'kept', badge: 'kept ✓' },
    { kind: 'file', name: 'b.csv', path: 'b.csv', state: 'kept', badge: 'kept ✓' },
    { kind: 'file', name: 'gone.dat', path: 'gone.dat', state: 'cleared', badge: 'cleared' },
  ],
}

describe('RunView durability summary + cleared toggle', () => {
  let origFetch: typeof globalThis.fetch
  beforeEach(() => { origFetch = globalThis.fetch; vi.useFakeTimers() })
  afterEach(() => { globalThis.fetch = origFetch; vi.useRealTimers(); vi.restoreAllMocks() })

  it('shows summary chips, hides cleared by default, toggle reveals them', async () => {
    globalThis.fetch = vi.fn().mockImplementation((url: string) =>
      String(url).includes('/durable')
        ? Promise.resolve({ ok: true, json: () => Promise.resolve(mixedTree) })
        : Promise.resolve({ ok: true, json: () => Promise.resolve({}) })) as unknown as typeof globalThis.fetch
    const run = { id: 'ana_2', type: 'analysis', title: 'R', metadata: {} } as unknown as never
    await act(async () => {
      render(<RunView run={run} entities={[]} onFocus={() => {}} onChange={() => {}} />)
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    expect(screen.getByText('2 kept')).toBeTruthy()
    expect(screen.getByText('1 cleared · show')).toBeTruthy()
    expect(screen.queryByText('gone.dat')).toBeNull()       // cleared hidden by default

    fireEvent.click(screen.getByText('1 cleared · show'))
    expect(screen.getByText('gone.dat')).toBeTruthy()       // revealed
    expect(screen.getByText('1 cleared · hide')).toBeTruthy()
  })
})
