/**
 * Component tests for the redesigned RevisionStrip — defaults to the
 * LATEST revision in the chain, swaps the displayed entity in-place
 * via chevrons, and (the critical behavior) shows a confirmation
 * dialog before issuing a "revise from non-latest" action.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import RevisionStrip, { useFigureRevisions } from './RevisionStrip'
import type { Entity } from '../types'

const ent = (id: string, ts: string): Entity => ({
  id,
  type: 'figure',
  title: `Figure ${id}`,
  status: 'active',
  artifact_path: `/artifacts/${id}.png`,
  created_at: ts,
  updated_at: ts,
} as unknown as Entity)


function Harness({ anchorId, onActionSpy }: { anchorId: string; onActionSpy: (a: string, eid: string) => void }) {
  const rev = useFigureRevisions(anchorId)
  return (
    <div>
      <div data-testid="displayed-id">{rev.displayed?.id ?? ''}</div>
      <RevisionStrip rev={rev} onAction={(a, e) => onActionSpy(a, e.id)} />
    </div>
  )
}


describe('RevisionStrip', () => {
  let origFetch: typeof globalThis.fetch
  beforeEach(() => { origFetch = globalThis.fetch })
  afterEach(() => { globalThis.fetch = origFetch; vi.restoreAllMocks() })

  function mockChain(chain: Entity[]) {
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/revisions')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            chain, position: 0, prev: chain[1]?.id ?? null, next: null,
          }),
        } as unknown as Response)
      }
      return Promise.reject(new Error('unexpected fetch'))
    }) as typeof globalThis.fetch
  }

  it('renders the SplitButton only when chain has 1 entry (no chevrons)', async () => {
    mockChain([ent('fig_a', '2026-01-01T00:00:00')])
    render(<Harness anchorId="fig_a" onActionSpy={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('displayed-id').textContent).toBe('fig_a'))
    expect(screen.queryByLabelText('Older revision')).toBeNull()
    expect(screen.queryByLabelText('Newer revision')).toBeNull()
    expect(screen.getByRole('button', { name: /💬 Chat/ })).toBeTruthy()
  })

  it('defaults the displayed entity to the LATEST revision (chain[0])', async () => {
    mockChain([
      ent('fig_c_latest', '2026-03-01T00:00:00'),
      ent('fig_b', '2026-02-01T00:00:00'),
      ent('fig_a_oldest', '2026-01-01T00:00:00'),
    ])
    render(<Harness anchorId="fig_a_oldest" onActionSpy={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('displayed-id').textContent).toBe('fig_c_latest'))
    // "rev 3 / 3" — the labels collapse text nodes so look for the number 3 twice
    expect(screen.getByTitle(/Revision 3 of 3/)).toBeTruthy()
  })

  it('swaps the displayed entity in-place when chevrons are clicked', async () => {
    mockChain([
      ent('fig_c_latest', '2026-03-01T00:00:00'),
      ent('fig_b', '2026-02-01T00:00:00'),
      ent('fig_a_oldest', '2026-01-01T00:00:00'),
    ])
    render(<Harness anchorId="fig_a_oldest" onActionSpy={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('displayed-id').textContent).toBe('fig_c_latest'))

    act(() => { fireEvent.click(screen.getByLabelText('Older revision')) })
    expect(screen.getByTestId('displayed-id').textContent).toBe('fig_b')
    act(() => { fireEvent.click(screen.getByLabelText('Older revision')) })
    expect(screen.getByTestId('displayed-id').textContent).toBe('fig_a_oldest')
    // At oldest, Older button is disabled
    expect((screen.getByLabelText('Older revision') as HTMLButtonElement).disabled).toBe(true)
  })

  it('Revise from LATEST fires action="revision" directly (no dialog)', async () => {
    mockChain([
      ent('fig_b_latest', '2026-02-01T00:00:00'),
      ent('fig_a', '2026-01-01T00:00:00'),
    ])
    const spy = vi.fn()
    render(<Harness anchorId="fig_a" onActionSpy={spy} />)
    await waitFor(() => expect(screen.getByTestId('displayed-id').textContent).toBe('fig_b_latest'))

    // Open SplitButton dropdown
    fireEvent.click(screen.getByTitle('More actions'))
    const reviseOpt = await screen.findByText('Make a revision')
    fireEvent.click(reviseOpt)
    expect(spy).toHaveBeenCalledWith('revision', 'fig_b_latest')
    // No confirmation dialog should be shown
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('Revise from NON-LATEST shows confirmation; confirm emits revision-supersede', async () => {
    mockChain([
      ent('fig_c_latest', '2026-03-01T00:00:00'),
      ent('fig_b', '2026-02-01T00:00:00'),
      ent('fig_a_oldest', '2026-01-01T00:00:00'),
    ])
    const spy = vi.fn()
    render(<Harness anchorId="fig_a_oldest" onActionSpy={spy} />)
    await waitFor(() => expect(screen.getByTestId('displayed-id').textContent).toBe('fig_c_latest'))

    // Step back to the oldest (rev 1 of 3) — there are 2 newer
    act(() => { fireEvent.click(screen.getByLabelText('Older revision')) })
    act(() => { fireEvent.click(screen.getByLabelText('Older revision')) })
    expect(screen.getByTestId('displayed-id').textContent).toBe('fig_a_oldest')

    // Open SplitButton dropdown and click Revise
    fireEvent.click(screen.getByTitle('More actions'))
    const reviseOpt = await screen.findByText('Make a revision…')
    fireEvent.click(reviseOpt)
    // Confirmation dialog appears
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent?.toLowerCase()).toContain('non-latest')
    expect(dialog.textContent).toMatch(/2 newer/)
    expect(spy).not.toHaveBeenCalled()  // not yet — user has to confirm

    // Confirm
    fireEvent.click(screen.getByText('Revise (supersede newer)'))
    expect(spy).toHaveBeenCalledWith('revision-supersede', 'fig_a_oldest')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('Cancel on confirmation closes the dialog without firing the action', async () => {
    mockChain([
      ent('fig_b_latest', '2026-02-01T00:00:00'),
      ent('fig_a', '2026-01-01T00:00:00'),
    ])
    const spy = vi.fn()
    render(<Harness anchorId="fig_a" onActionSpy={spy} />)
    await waitFor(() => expect(screen.getByTestId('displayed-id').textContent).toBe('fig_b_latest'))
    act(() => { fireEvent.click(screen.getByLabelText('Older revision')) })
    fireEvent.click(screen.getByTitle('More actions'))
    fireEvent.click(await screen.findByText('Make a revision…'))
    expect(screen.getByRole('dialog')).toBeTruthy()
    fireEvent.click(screen.getByText('Cancel'))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(spy).not.toHaveBeenCalled()
  })

  it('shows " · latest" indicator when at chain[0]; " · not latest" otherwise', async () => {
    mockChain([
      ent('fig_b_latest', '2026-02-01T00:00:00'),
      ent('fig_a', '2026-01-01T00:00:00'),
    ])
    render(<Harness anchorId="fig_a" onActionSpy={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('displayed-id').textContent).toBe('fig_b_latest'))
    // At chain[0] (latest) — should see " · latest"
    expect(screen.getByTitle(/Revision 2 of 2/).textContent).toContain('latest')
    expect(screen.getByTitle(/Revision 2 of 2/).textContent).not.toContain('not latest')
    // Step back to older — should see " · not latest"
    act(() => { fireEvent.click(screen.getByLabelText('Older revision')) })
    expect(screen.getByTitle(/Revision 1 of 2/).textContent).toContain('not latest')
  })

  it('ArrowLeft/ArrowRight on a chevron steps through revisions (keyboard nav)', async () => {
    mockChain([
      ent('fig_c', '2026-03-01T00:00:00'),
      ent('fig_b', '2026-02-01T00:00:00'),
      ent('fig_a', '2026-01-01T00:00:00'),
    ])
    render(<Harness anchorId="fig_a" onActionSpy={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('displayed-id').textContent).toBe('fig_c'))
    const olderBtn = screen.getByLabelText('Older revision')
    // ArrowLeft → older
    act(() => { fireEvent.keyDown(olderBtn, { key: 'ArrowLeft' }) })
    expect(screen.getByTestId('displayed-id').textContent).toBe('fig_b')
    act(() => { fireEvent.keyDown(olderBtn, { key: 'ArrowLeft' }) })
    expect(screen.getByTestId('displayed-id').textContent).toBe('fig_a')
    // At oldest — ArrowLeft is a no-op (canGoPrev=false)
    act(() => { fireEvent.keyDown(olderBtn, { key: 'ArrowLeft' }) })
    expect(screen.getByTestId('displayed-id').textContent).toBe('fig_a')
    // ArrowRight → newer
    act(() => { fireEvent.keyDown(olderBtn, { key: 'ArrowRight' }) })
    expect(screen.getByTestId('displayed-id').textContent).toBe('fig_b')
    act(() => { fireEvent.keyDown(olderBtn, { key: 'ArrowRight' }) })
    expect(screen.getByTestId('displayed-id').textContent).toBe('fig_c')
    // Right arrow from newer chevron also works
    const newerBtn = screen.getByLabelText('Newer revision')
    act(() => { fireEvent.keyDown(newerBtn, { key: 'ArrowLeft' }) })
    expect(screen.getByTestId('displayed-id').textContent).toBe('fig_b')
  })

  it('gallery: clicking rev N/N pill opens a thumbnail strip; clicking a thumb jumps the displayed revision', async () => {
    mockChain([
      ent('fig_c_latest', '2026-03-01T00:00:00'),
      ent('fig_b', '2026-02-01T00:00:00'),
      ent('fig_a_oldest', '2026-01-01T00:00:00'),
    ])
    render(<Harness anchorId="fig_a_oldest" onActionSpy={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('displayed-id').textContent).toBe('fig_c_latest'))

    // No gallery before the user clicks the pill
    expect(screen.queryByRole('dialog', { name: /Revision gallery/i })).toBeNull()

    // Click the "rev 3/3" pill
    fireEvent.click(screen.getByTitle(/Revision 3 of 3/))
    const gallery = screen.getByRole('dialog', { name: /Revision gallery/i })
    expect(gallery).toBeTruthy()
    const thumbs = gallery.querySelectorAll('.rev-strip__gallery-thumb')
    expect(thumbs.length).toBe(3)
    expect(thumbs[0].getAttribute('title')).toContain('rev 1 of 3')
    expect(thumbs[2].getAttribute('title')).toContain('latest')
    // The selected (latest) thumb is last; carries the marker class.
    expect(thumbs[2].className).toContain('rev-strip__gallery-thumb--selected')

    // Click the OLDEST thumb (rev 1) — displayed should jump to fig_a_oldest.
    fireEvent.click(thumbs[0])
    expect(screen.getByTestId('displayed-id').textContent).toBe('fig_a_oldest')
    // Picking auto-closes the gallery.
    expect(screen.queryByRole('dialog', { name: /Revision gallery/i })).toBeNull()
  })

  it('gallery: Escape closes; outside-click closes', async () => {
    mockChain([
      ent('fig_b', '2026-02-01T00:00:00'),
      ent('fig_a', '2026-01-01T00:00:00'),
    ])
    render(<Harness anchorId="fig_a" onActionSpy={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('displayed-id').textContent).toBe('fig_b'))

    fireEvent.click(screen.getByTitle(/Revision 2 of 2/))
    expect(screen.getByRole('dialog', { name: /Revision gallery/i })).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: /Revision gallery/i })).toBeNull()

    fireEvent.click(screen.getByTitle(/Revision 2 of 2/))
    expect(screen.getByRole('dialog', { name: /Revision gallery/i })).toBeTruthy()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('dialog', { name: /Revision gallery/i })).toBeNull()
  })

  it('Reproduce always fires immediately (no dialog), even from non-latest', async () => {
    mockChain([
      ent('fig_b_latest', '2026-02-01T00:00:00'),
      ent('fig_a', '2026-01-01T00:00:00'),
    ])
    const spy = vi.fn()
    render(<Harness anchorId="fig_a" onActionSpy={spy} />)
    await waitFor(() => expect(screen.getByTestId('displayed-id').textContent).toBe('fig_b_latest'))
    act(() => { fireEvent.click(screen.getByLabelText('Older revision')) })

    fireEvent.click(screen.getByTitle('More actions'))
    fireEvent.click(await screen.findByText('Reproduce'))
    expect(spy).toHaveBeenCalledWith('reproduce', 'fig_a')
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
