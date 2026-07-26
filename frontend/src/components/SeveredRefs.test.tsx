/**
 * The severed-reference notice. The delete route stamps every SURVIVING
 * neighbor of a hard-deleted entity with `metadata.severed_refs` so a gap in
 * the graph is recorded rather than silent — but nothing rendered it, so the
 * stamp was write-only and the user still saw a dangling nothing. This is the
 * reader.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import SeveredRefs from './SeveredRefs'

const stamp = (over: Record<string, unknown> = {}) => ({
  id: 'res_1', type: 'result', title: 'Result A',
  at: 1785000000, rel: 'includes', dir: 'in', ...over,
})

describe('SeveredRefs', () => {
  it('names what vanished and how it was joined', () => {
    render(<SeveredRefs refs={[stamp()]} />)
    expect(screen.getByText(/Result A/)).toBeTruthy()
    expect(screen.getByText(/includes/)).toBeTruthy()
  })

  it('renders nothing when there are no stamps', () => {
    const { container } = render(<SeveredRefs refs={[]} />)
    expect(container.textContent).toBe('')
  })

  it('survives a malformed stamp instead of blanking the card', () => {
    // Stamps are historical metadata: an old or partial one must not throw.
    const { container } = render(
      <SeveredRefs refs={[{ id: 'x' } as never, stamp()]} />)
    expect(container.textContent).toContain('Result A')
  })

  it('summarises rather than listing when many refs were severed', () => {
    const many = Array.from({ length: 7 }, (_, i) =>
      stamp({ id: `r${i}`, title: `Gone ${i}` }))
    render(<SeveredRefs refs={many} />)
    expect(screen.getByText(/Gone 0/)).toBeTruthy()
    expect(screen.queryByText(/Gone 6/)).toBeNull()   // capped
    expect(screen.getByText(/more/)).toBeTruthy()
  })
})
