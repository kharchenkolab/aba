/**
 * Unpin flow: the last-content case is a COMPLETABLE destructive confirm
 * (unpin + archive the wrapping Result — the backend /unpin contract),
 * never a dead-end info dialog; cancelling performs no request. And the
 * chat FigurePin only flips optimistically when PINNING — a cancelled
 * unpin leaves the pin red (the discordant-state regression, PK 2026-07-17).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useUnpinConfirm } from './useUnpinConfirm'
import { FigurePin } from '../bio/Message'
import type { Entity } from '../types'

const fig = { id: 'fig1', type: 'figure', title: 'trend plot', status: 'active',
  metadata: {} } as unknown as Entity
const wrapOne = { id: 'res1', type: 'result', title: 'R', status: 'active',
  metadata: { primary_evidence_id: 'fig1',
              members: [{ kind: 'figure', ref: 'fig1' }] } } as unknown as Entity
const wrapMany = { id: 'res2', type: 'result', title: 'R2', status: 'active',
  metadata: { primary_evidence_id: 'fig1',
              members: [{ kind: 'figure', ref: 'fig1' },
                        { kind: 'figure', ref: 'fig2' }] } } as unknown as Entity

function Host({ entities }: { entities: Entity[] }) {
  const { requestUnpin, dialog } = useUnpinConfirm(entities, () => {})
  return <div><button onClick={() => requestUnpin('fig1', 'trend plot')}>go</button>{dialog}</div>
}

describe('useUnpinConfirm', () => {
  let origFetch: typeof globalThis.fetch
  beforeEach(() => {
    origFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true }) as unknown as typeof globalThis.fetch
  })
  afterEach(() => { globalThis.fetch = origFetch; vi.restoreAllMocks() })

  it('last-content unpin is a completable destructive confirm that ACTS', async () => {
    render(<Host entities={[fig, wrapOne]} />)
    fireEvent.click(screen.getByText('go'))
    expect(screen.getByText('Unpin & archive Result')).toBeTruthy()   // not a dead end
    expect(screen.queryByText(/⋯ menu/)).toBeNull()
    fireEvent.click(screen.getByText('Unpin & archive Result'))
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/entities/fig1/unpin', expect.objectContaining({ method: 'POST' }))
  })

  it('cancel performs NO request (state stays consistent)', () => {
    render(<Host entities={[fig, wrapOne]} />)
    fireEvent.click(screen.getByText('go'))
    fireEvent.click(screen.getByText('Cancel'))
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('multi-member Result gets the member-removal confirm', () => {
    render(<Host entities={[fig, wrapMany]} />)
    fireEvent.click(screen.getByText('go'))
    expect(screen.getByText('Unpin')).toBeTruthy()
    expect(screen.getByText(/only the Result-membership is dropped/)).toBeTruthy()
  })
})

describe('FigurePin optimism', () => {
  it('pinning flips instantly; clicking a PINNED pin does NOT un-red it', () => {
    const onPin = vi.fn()
    const { rerender } = render(<FigurePin entity={fig} isPinned={false} onPin={onPin} />)
    const btn = () => document.querySelector('.msg__tool--pin')!
    fireEvent.click(btn())
    expect(btn().className).toContain('msg__tool--pinned')     // optimistic pin ✓
    // now authoritative pinned; a click (unpin request → dialog) must NOT flip
    rerender(<FigurePin entity={fig} isPinned={true} onPin={onPin} />)
    fireEvent.click(btn())
    expect(btn().className).toContain('msg__tool--pinned')     // stays red until real unpin
    expect(onPin).toHaveBeenLastCalledWith('fig1', false)
  })
})
