/**
 * Storyboard smoke: every scene's world renders through the shared Record
 * renderer without throwing, and each scene shows its load-bearing element.
 * This is the guard on the world-parameterization refactor: a fixture key
 * the renderer no longer reads (or vice versa) fails here, not on stage.
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import Record from '../notebook/Record'
import Storyboard from './Storyboard'
import { SCENES } from './scenes'

// element.scrollIntoView is not implemented in jsdom
window.HTMLElement.prototype.scrollIntoView = () => {}

describe('workflow storyboard', () => {
  it('renders every scene without throwing', () => {
    for (const s of SCENES) {
      const { unmount } = render(<Record world={s.world} />)
      unmount()
    }
  })

  it('E1 is the composer-only day-0 face', () => {
    const { getByPlaceholderText, unmount } = render(<Record world={SCENES[0].world} />)
    expect(getByPlaceholderText(/What are we working with/)).toBeTruthy()
    unmount()
  })

  it('E2 shows the working panel and the first sediment line', () => {
    const { getByText, unmount } = render(<Record world={SCENES[1].world} />)
    getByText('working session')
    getByText(/recorded in the sediment the moment it launched/)
    unmount()
  })

  it('E4 renders the stub question section', () => {
    const { getAllByText, getByText, unmount } = render(<Record world={SCENES[3].world} />)
    expect(getAllByText('Is the calibration stable across seasons?').length).toBeGreaterThan(0)
    getByText(/Nothing ratified yet/)
    unmount()
  })

  it('M4 shows the session-close distillation face', () => {
    const { getByText, unmount } = render(<Record world={SCENES[8].world} />)
    getByText(/session close — the distillation moment/)
    getByText('file & close')
    unmount()
  })

  it('M5 files the transcript under its question and sediment lines', () => {
    const { getAllByText, getByText, unmount } = render(<Record world={SCENES[9].world} />)
    getByText(/⟲ winter dig · Jul 20 · 5 runs · 1 fragment · 1 draft — transcript/)
    expect(getAllByText(/⟲ winter dig/).length).toBeGreaterThan(1) // section + sediment ⟲ chips
    unmount()
  })

  it('the plain notebook world still renders (default Record)', () => {
    const { getAllByText, getByText, unmount } = render(<Record />)
    expect(getAllByText('Coastal sensor study').length).toBeGreaterThan(0)
    getByText('the story so far')
    unmount()
  })

  it('the storyboard chrome mounts with the bar and caption', () => {
    const { getByText, unmount } = render(<Storyboard />)
    getByText(/a scientist's workflow/)
    getByText('E1')
    unmount()
  })
})
