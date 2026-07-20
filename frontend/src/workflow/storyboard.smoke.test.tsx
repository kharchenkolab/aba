/**
 * Storyboard smoke: every scene's world renders through the shared Record
 * renderer without throwing, and each scene shows its load-bearing element.
 * This is the guard on the world-parameterization refactor: a fixture key
 * the renderer no longer reads (or vice versa) fails here, not on stage.
 */
import { describe, it, expect } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
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
    getByText(/winter dig · Jul 20 · 5 runs · 1 fragment · 1 draft — transcript/)
    expect(getAllByText(/^winter dig$/).length + getAllByText(/winter dig/).length).toBeGreaterThan(1) // section + sediment session chips
    unmount()
  })

  it('M5 renders the work record at session grain with leftovers counted', () => {
    const { getByText, unmount } = render(<Record world={SCENES[9].world} />)
    getByText('by session')
    getByText(/2 unexamined/)
    getByText(/outside sessions/)
    unmount()
  })

  it('M6 opens the session page: distillate, leftovers shelf, addressable turns, continue composer', () => {
    const { getByText, getByPlaceholderText, unmount } = render(<Record world={SCENES[10].world} />)
    getByText(/what entered the record from here/)
    getByText(/leftovers — produced here, never pinned/)
    getByText(/may bear on Q2/)
    getByText(/continues ←/)
    getByPlaceholderText(/Continue this line/)
    unmount()
  })

  it('transcript search finds what was SAID and tags the session stratum', () => {
    // covered via the world's sessions: "never serviced" occurs only in the
    // winter-dig transcript, not in any stratum text
    const { getByPlaceholderText, container, unmount } = render(<Record world={SCENES[9].world} />)
    const input = getByPlaceholderText('search the record…') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'never serviced' } })
    const hit = container.querySelector('.toc__hit-stratum--session')
    expect(hit).toBeTruthy()
    expect(hit!.parentElement!.textContent).toContain('winter dig')
    unmount()
  })

  it('M7 shows live anchoring: standing anchor, TOC deltas, impact set, looking-at, cross proposal', () => {
    const { getByText, container, unmount } = render(<Record world={SCENES[11].world} />)
    getByText(/winter dig · working here/)               // standing anchor state
    getByText(/touched:/)                                 // impact set
    getByText(/looking at:/)                              // deixis doc → chat
    getByText('show T1 on the page →')                    // deixis chat → doc
    getByText('file a note → Q2')                         // cross-boundary proposal
    expect(container.querySelector('.toc__delta--condition')).toBeTruthy()
    expect(container.querySelector('.toc__delta--accretion')).toBeTruthy()
    unmount()
  })

  it('M8 is the scale + triage face: band slots, tray parity, dormant/stalled compaction', () => {
    const { getByText, container, unmount } = render(<Record world={SCENES[12].world} />)
    getByText(/4 need you/)                                       // band count (derived)
    fireEvent.click(getByText(/4 need you/))
    expect(container.querySelectorAll('.tray__row').length).toBe(4)  // tray parity with the count
    fireEvent.click(getByText(/file all routine/))
    getByText(/3 need you/)                                       // batch-file drops the count
    getByText('✓ filed')                                          // …and flips the in-place badge
    expect(container.querySelectorAll('.nsec--dormant').length).toBe(6)
    expect(container.querySelectorAll('.trail--folded').length).toBe(2)
    getByText(/214 runs · complete · automatic/)
    getByText(/⚡ contradiction/)
    unmount()
  })

  it('M9 is the spine: arcs fold, faces follow state, epitaphs, roll-up periphery, parity', () => {
    const m9 = SCENES.find(s => s.id === 'm9')!
    const { getByText, getAllByText, container, unmount } = render(<Record world={m9.world} />)
    getByText(/rolling synthesis · drafted by Guide/)              // the ratified abstract
    expect(container.querySelectorAll('.arc').length).toBe(4)      // four arcs
    expect(container.querySelectorAll('.arc--folded').length).toBe(2)  // A1 + A4 fold whole
    // open faces render; folded arcs' questions don't
    expect(container.querySelectorAll('.spq--openq').length).toBe(3)
    expect(container.querySelectorAll('.spq--dead').length).toBe(1)    // only A2's epitaph visible
    fireEvent.click(getAllByText('Calibration & drift').find(el => el.closest('.arc__head'))!)  // unfold A1
    expect(container.querySelectorAll('.spq--dead').length).toBe(3)
    getByText(/ruled out — cross-vendor replication/)
    // pending rides the question lines: band count = tray rows (parity)
    getByText(/2 need you/)
    fireEvent.click(getByText(/2 need you/))
    expect(container.querySelectorAll('.tray__row').length).toBe(2)
    // roll-up: the arc TOC entries carry aggregated badges
    expect(container.querySelector('.toc__rollup')).toBeTruthy()
    // a folded arc is an ABSTRACT, not a blank: it shows what it holds
    getByText(/STL detrending adopted throughout/)
    // the rolling abstract cites its arcs as live refs
    expect(container.querySelectorAll('.ref--arc').length).toBe(3)
    // the archive declares itself
    expect(getAllByText(/1,847 runs/).length).toBeGreaterThan(0)
    unmount()
  })

  it('M9 epitaphs are searchable — "did we ever try X?" answers with the killing run', () => {
    const m9 = SCENES.find(s => s.id === 'm9')!
    const { getByPlaceholderText, container, unmount } = render(<Record world={m9.world} />)
    const input = getByPlaceholderText('search the record…') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'gap-filling' } })
    const hit = container.querySelector('.toc__hit-stratum--epitaph')
    expect(hit).toBeTruthy()
    expect(hit!.parentElement!.textContent).toContain('R148')
    unmount()
  })

  it('M9 descends: open ▸ on the winter question advances; M10 wears the crumb', () => {
    const m9 = SCENES.find(s => s.id === 'm9')!
    let advanced = ''
    const { getAllByText, unmount } = render(<Record world={m9.world} onAdvance={t => { advanced = t }} />)
    const q22row = getAllByText('open ▸').find(b => b.closest('.spq')?.textContent?.includes('winter anomaly recur'))!
    fireEvent.click(q22row)
    expect(advanced).toBe('descend:q22')
    unmount()
    const m10 = SCENES.find(s => s.id === 'm10')!
    const r2 = render(<Record world={m10.world} />)
    r2.getByText(/‹ Coastal sensor study/)                         // the breadcrumb up
    r2.getAllByText(/Does the winter anomaly recur/)               // the question IS the page title
    r2.getByText('the story so far')                               // …and the full face lives here
    r2.unmount()
  })

  it("what's-new items are doors (clickable when they have a target)", () => {
    const { getByText, container, unmount } = render(<Record world={SCENES[9].world} />)
    fireEvent.click(container.querySelector('.wnew__head')!)
    const door = getByText(/addendum drafted for Q1/).closest('button')
    expect(door).toBeTruthy()
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
