/**
 * findRevisionHead — pure helper that walks `metadata.revision_of` edges
 * over a flat entities array and returns the active leaf (= chain head).
 *
 * Plus an integration smoke: useFigureRevisions with `entities` passed in
 * returns the HEAD as `displayed` on first paint (before the /revisions
 * fetch ever resolves), eliminating the v0 flash.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render } from '@testing-library/react'
import { findRevisionHead, useFigureRevisions } from './RevisionStrip'
import type { Entity } from '../types'

const fig = (id: string, opts: Partial<Entity> = {}): Entity => ({
  id,
  type: 'figure',
  title: id,
  status: 'active',
  artifact_path: `/x/${id}.png`,
  created_at: '2026-01-01T00:00:00',
  updated_at: '2026-01-01T00:00:00',
  metadata: {},
  ...opts,
} as unknown as Entity)

describe('findRevisionHead', () => {
  it('returns the anchor when no descendants', () => {
    const a = fig('a')
    const head = findRevisionHead('a', [a])
    expect(head?.id).toBe('a')
  })

  it('walks one step', () => {
    const a = fig('a')
    const b = fig('b', { metadata: { revision_of: 'a' }, created_at: '2026-01-02T00:00:00' })
    const head = findRevisionHead('a', [a, b])
    expect(head?.id).toBe('b')
  })

  it('walks multiple steps', () => {
    const a = fig('a')
    const b = fig('b', { metadata: { revision_of: 'a' }, created_at: '2026-01-02T00:00:00' })
    const c = fig('c', { metadata: { revision_of: 'b' }, created_at: '2026-01-03T00:00:00' })
    const head = findRevisionHead('a', [a, b, c])
    expect(head?.id).toBe('c')
  })

  it('picks newest sibling at branch points', () => {
    const a = fig('a')
    const b = fig('b', { metadata: { revision_of: 'a' }, created_at: '2026-01-02T00:00:00' })
    const c = fig('c', { metadata: { revision_of: 'a' }, created_at: '2026-01-05T00:00:00' })
    const head = findRevisionHead('a', [a, b, c])
    expect(head?.id).toBe('c')
  })

  it('skips superseded descendants', () => {
    const a = fig('a')
    const b = fig('b', {
      status: 'superseded',
      metadata: { revision_of: 'a' },
      created_at: '2026-01-02T00:00:00',
    })
    const head = findRevisionHead('a', [a, b])
    expect(head?.id).toBe('a')
  })

  it('returns undefined when anchor missing', () => {
    expect(findRevisionHead('missing', [fig('a')])).toBeUndefined()
  })

  it('terminates on a cycle', () => {
    const a = fig('a', { metadata: { revision_of: 'b' } })
    const b = fig('b', { metadata: { revision_of: 'a' } })
    // walk from a → finds b (parent=a child? no, b.revision_of=a so b is a's
    // child) → returns b. Then b's child is a, already seen → bail.
    const head = findRevisionHead('a', [a, b])
    expect(['a', 'b']).toContain(head?.id)
  })
})


// ── Hook integration: first paint already shows head ──────────────────
beforeEach(() => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(
    // Hold the /revisions fetch forever — we want to assert the FIRST
    // paint comes from entities, not from the backend.
    () => new Promise(() => {}) as unknown as Promise<Response>,
  )
})
afterEach(() => { vi.restoreAllMocks() })


function HookProbe({ anchorId, entities }: { anchorId: string; entities: Entity[] }) {
  const rev = useFigureRevisions(anchorId, 0, entities)
  return <span data-testid="displayed" data-url={rev.displayed?.artifact_path ?? ''} />
}

describe('useFigureRevisions — first paint with entities', () => {
  it('renders the chain head (latest) without waiting for /revisions', () => {
    const a = fig('a')
    const b = fig('b', { metadata: { revision_of: 'a' }, created_at: '2026-01-02T00:00:00' })
    const { getByTestId } = render(<HookProbe anchorId="a" entities={[a, b]} />)
    expect(getByTestId('displayed').getAttribute('data-url')).toBe('/x/b.png')
  })

  it('renders anchor when no revisions exist', () => {
    const a = fig('a')
    const { getByTestId } = render(<HookProbe anchorId="a" entities={[a]} />)
    expect(getByTestId('displayed').getAttribute('data-url')).toBe('/x/a.png')
  })

  it('falls back to anchor when entities omitted', () => {
    const { getByTestId } = render(<HookProbe anchorId="a" entities={[]} />)
    // No entities and no chain → displayed is null; data-url is ''.
    expect(getByTestId('displayed').getAttribute('data-url')).toBe('')
  })
})
