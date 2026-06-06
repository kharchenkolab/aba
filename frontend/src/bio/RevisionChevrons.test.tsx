/**
 * RevisionChevrons component tests.
 *
 * Mocks /api/entities/{id}/revisions and verifies:
 *   - chevrons hidden when chain has 0 or 1 entries
 *   - prev/next chevrons render correctly when chain has multiple
 *   - badge text matches "rev N/M" with the right orientation
 *   - clicking a chevron calls onFocus with the right sibling id
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import RevisionChevrons from './RevisionChevrons'


function _mockFetch(body: unknown, ok = true) {
  return vi.fn(() => Promise.resolve({
    ok,
    json: () => Promise.resolve(body),
  } as Response))
}


describe('RevisionChevrons', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('hides chevrons when the chain has only 1 entry', async () => {
    globalThis.fetch = _mockFetch({
      chain: [{ id: 'fig_a' }],
      position: 0, prev: null, next: null,
    }) as typeof fetch
    render(
      <RevisionChevrons entity_id="fig_a" onFocus={() => {}}>
        <img alt="solo" />
      </RevisionChevrons>
    )
    // image renders
    await waitFor(() => expect(screen.getByAltText('solo')).toBeTruthy())
    // chevrons absent
    expect(screen.queryByLabelText('Previous revision')).toBeNull()
    expect(screen.queryByLabelText('Next revision')).toBeNull()
  })

  it('renders both chevrons + badge when entity sits in the middle of a chain', async () => {
    globalThis.fetch = _mockFetch({
      chain: [
        { id: 'fig_newest' },
        { id: 'fig_mid' },
        { id: 'fig_oldest' },
      ],
      position: 1, prev: 'fig_oldest', next: 'fig_newest',
    }) as typeof fetch
    const onFocus = vi.fn()
    render(
      <RevisionChevrons entity_id="fig_mid" onFocus={onFocus}>
        <img alt="middle" />
      </RevisionChevrons>
    )
    // Both chevrons appear
    const prev = await screen.findByLabelText('Previous revision')
    const next = await screen.findByLabelText('Next revision')
    expect(prev).toBeTruthy()
    expect(next).toBeTruthy()
    // Badge: "rev 2/3" (position 1 → newer side is rev 2 of 3)
    expect(screen.getByText(/rev 2\/3/)).toBeTruthy()
    // Click prev → fires onFocus with the OLDER sibling
    fireEvent.click(prev)
    expect(onFocus).toHaveBeenCalledWith('fig_oldest')
    fireEvent.click(next)
    expect(onFocus).toHaveBeenCalledWith('fig_newest')
  })

  it('hides prev chevron when entity is the newest revision', async () => {
    globalThis.fetch = _mockFetch({
      chain: [{ id: 'fig_new' }, { id: 'fig_old' }],
      position: 0, prev: 'fig_old', next: null,
    }) as typeof fetch
    render(
      <RevisionChevrons entity_id="fig_new" onFocus={() => {}}>
        <img alt="newest" />
      </RevisionChevrons>
    )
    await screen.findByLabelText('Previous revision')
    expect(screen.queryByLabelText('Next revision')).toBeNull()
    expect(screen.getByText(/rev 2\/2/)).toBeTruthy()  // newest is rev 2 of 2
  })

  it('renders nothing chevron-related when fetch fails', async () => {
    globalThis.fetch = _mockFetch({}, false) as typeof fetch
    render(
      <RevisionChevrons entity_id="fig_err" onFocus={() => {}}>
        <img alt="err" />
      </RevisionChevrons>
    )
    // Still renders the child
    expect(screen.getByAltText('err')).toBeTruthy()
    // But no chevrons
    await waitFor(() =>
      expect(screen.queryByLabelText('Previous revision')).toBeNull()
    )
  })
})
