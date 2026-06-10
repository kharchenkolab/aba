/**
 * Tests for the chat ArtifactPin button (Option B / Phase 3).
 *
 * It POSTs to /api/artifacts/{exec_id}/{kind}/{idx}/pin and flips to a
 * "pinned" visual state on success. Malformed artifact_ids log + no-op.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import Message from '../bio/Message'
import type { Message as Msg, Block } from '../types'


function _mockFetch(ok = true, body: unknown = {}) {
  return vi.fn(() => Promise.resolve({
    ok,
    text: async () => 'err',
    json: async () => body,
  } as Response))
}


describe('ArtifactPin (chat-level pin for unpinned artifacts)', () => {
  it('appears on image blocks with artifact_id when no Entity exists', () => {
    const blocks: Block[] = [
      { type: 'image', url: '/u.png', alt: 'umap.png',
        artifact_id: 'exec_a:figure:0' },
    ]
    const m: Msg = { id: 'm1', role: 'assistant', blocks }
    render(<Message message={m} entities={[]} onPin={() => {}}
                    pinnedFigureIds={new Set()} keptKeys={new Set()} />)
    const btns = screen.getAllByTitle(/Pin this figure/)
    expect(btns.length).toBe(1)
  })

  it('does not appear when an Entity exists for the same url (FigurePin takes over)', () => {
    const blocks: Block[] = [
      { type: 'image', url: '/u.png', alt: 'umap.png',
        artifact_id: 'exec_a:figure:0' },
    ]
    const m: Msg = { id: 'm1', role: 'assistant', blocks }
    const ent = {
      id: 'fig_x', type: 'figure', title: 'Umap', status: 'active',
      artifact_path: '/u.png',
      producing_params: null, parent_entity_id: null, scenario_of: null,
      metadata: null, tags: [], notes: null, pinned: false,
      exec_id: 'exec_a', artifact_kind: 'figure', artifact_idx: 0,
      deleted_at: null, created_at: '', updated_at: '',
    } as unknown as Parameters<typeof Message>[0]['entities'] extends infer T ? T extends (infer U)[] ? U : never : never
    render(<Message message={m} entities={[ent]} onPin={() => {}}
                    pinnedFigureIds={new Set()} keptKeys={new Set()} />)
    // Legacy FigurePin button is shown; we just check the count is 1
    // (the page has one Pin button); ArtifactPin is not added.
    const btns = screen.getAllByTitle(/Pin this figure|Pin/)
    expect(btns.length).toBe(1)
  })

  it('does NOT appear when neither entity nor artifact_id is available', () => {
    const blocks: Block[] = [
      { type: 'image', url: '/u.png', alt: 'umap.png' },  // no artifact_id
    ]
    const m: Msg = { id: 'm1', role: 'assistant', blocks }
    render(<Message message={m} entities={[]} onPin={() => {}}
                    pinnedFigureIds={new Set()} keptKeys={new Set()} />)
    expect(screen.queryByTitle(/Pin this figure/)).toBeNull()
  })

  it('clicking POSTs to /api/artifacts/{exec_id}/{kind}/{idx}/pin', async () => {
    const fetchMock = _mockFetch(true, { entity_id: 'fig_xx', was_new: true })
    globalThis.fetch = fetchMock as typeof fetch
    const blocks: Block[] = [
      { type: 'image', url: '/u.png', alt: 'umap.png',
        artifact_id: 'exec_zzz:figure:2' },
    ]
    const m: Msg = { id: 'm1', role: 'assistant', blocks }
    render(<Message message={m} entities={[]} onPin={() => {}}
                    pinnedFigureIds={new Set()} keptKeys={new Set()} />)
    const btn = screen.getByTitle(/Pin this figure/)
    fireEvent.click(btn)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const url = (fetchMock.mock.calls[0][0] as string)
    expect(url).toContain('/api/artifacts/exec_zzz/figure/2/pin')
    // Optimistic state flip
    await waitFor(() => {
      expect(screen.queryByTitle(/^Pinned$/)).toBeTruthy()
    })
  })
})
