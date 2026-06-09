/**
 * Highlight gesture on the focused Result.
 *
 * Mirrors the Threads/Message highlight UX in ResultView:
 *   1. A single Highlight toggle button lives next to the title.
 *   2. Clicking it activates highlight mode (button gets --on class).
 *   3. While active, hovering a MemberPanel reveals the yellow surface
 *      overlay (.rv-panel__hl) with the "draw to highlight" hint.
 *   4. Mousedown on the surface + mousemove + mouseup triggers
 *      captureHighlight, which calls onAnnotate with {image, note}
 *      and exits highlight mode.
 *
 * We mock html2canvas to avoid running the rasterizer in happy-dom.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import ResultView from './ResultView'
import type { Entity } from '../types'


// ── html2canvas stub ──────────────────────────────────────────────────────
// Returns a tiny canvas-like object whose toDataURL gives a fixed b64.
vi.mock('html2canvas', () => {
  const fakeCanvas = {
    width: 32, height: 32,
    getContext: () => ({
      drawImage: vi.fn(),
      beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(),
      stroke: vi.fn(),
      strokeStyle: '', lineWidth: 0, lineCap: '', lineJoin: '',
    }),
    toDataURL: () => 'data:image/png;base64,FAKEHIGHLIGHT',
  }
  return { default: vi.fn(async () => fakeCanvas) }
})

// Stub HTMLCanvasElement.getContext for the cropC + downscale canvases we
// create with document.createElement('canvas') (happy-dom returns null).
beforeEach(() => {
  const proto = HTMLCanvasElement.prototype as unknown as {
    getContext: () => CanvasRenderingContext2D | null
    toDataURL: () => string
  }
  proto.getContext = vi.fn(() => ({
    drawImage: vi.fn(),
    beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(),
    stroke: vi.fn(),
    strokeStyle: '', lineWidth: 0, lineCap: '', lineJoin: '',
  } as unknown as CanvasRenderingContext2D))
  proto.toDataURL = vi.fn(() => 'data:image/png;base64,FAKEHIGHLIGHT')
  // fetch mock for any unrelated PATCH/GET the component fires
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : (input as Request).url
    if (url.includes('/revisions')) {
      return new Response(JSON.stringify({ chain: [], total: 1 }),
                          { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
})
afterEach(() => { vi.restoreAllMocks() })


// ── fixtures ──────────────────────────────────────────────────────────────
const fig = (id: string, title?: string): Entity => ({
  id, type: 'figure', title: title ?? `Figure ${id}`,
  status: 'active',
  artifact_path: `/artifacts/${id}.png`,
  created_at: '2026-01-01T00:00:00',
  updated_at: '2026-01-01T00:00:00',
} as unknown as Entity)

function makeResult(ref: string): Entity {
  return {
    id: 'res_h1',
    type: 'result',
    title: 'Highlight target result',
    status: 'active',
    artifact_path: null,
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
    metadata: {
      thread_id: 'thr_t',
      members: [{ id: 'm_1', kind: 'figure', ref }],
      interpretation: '',
    },
  } as unknown as Entity
}


describe('ResultView — Highlight tool', () => {
  it('hides the toggle when onAnnotate is not provided', () => {
    const f = fig('fig_a')
    const result = makeResult('fig_a')
    render(<ResultView result={result} entities={[f]} onChange={() => {}} onFocus={() => {}} />)
    expect(document.querySelector('.hl-toggle')).toBeNull()
  })

  it('shows the toggle next to the title when onAnnotate is wired', () => {
    const f = fig('fig_a')
    const result = makeResult('fig_a')
    render(<ResultView result={result} entities={[f]} onChange={() => {}} onFocus={() => {}}
                       onAnnotate={() => {}} />)
    const head = document.querySelector('.rv__head')
    expect(head).not.toBeNull()
    expect(head?.querySelector('.hl-toggle')).not.toBeNull()
    expect(head?.querySelector('.rv__title')).not.toBeNull()
  })

  it('toggling adds .hl-toggle--on and reveals the surface on hover', () => {
    const f = fig('fig_a')
    const result = makeResult('fig_a')
    render(<ResultView result={result} entities={[f]} onChange={() => {}} onFocus={() => {}}
                       onAnnotate={() => {}} />)
    const btn = document.querySelector('.hl-toggle') as HTMLButtonElement
    expect(btn.classList.contains('hl-toggle--on')).toBe(false)
    // Before toggle: no surface even on hover
    const panel = document.querySelector('.rv-panel') as HTMLElement
    fireEvent.mouseEnter(panel)
    expect(panel.querySelector('.rv-panel__hl')).toBeNull()
    // Toggle on
    fireEvent.click(btn)
    expect(btn.classList.contains('hl-toggle--on')).toBe(true)
    // Re-trigger hover (the prior mouseEnter was while inactive)
    fireEvent.mouseLeave(panel)
    fireEvent.mouseEnter(panel)
    expect(panel.querySelector('.rv-panel__hl')).not.toBeNull()
    expect(panel.querySelector('.rv-panel__hl-hint')?.textContent).toContain('draw to highlight')
  })

  it('drawing a stroke calls onAnnotate({image, note}) and exits highlight mode', async () => {
    const f = fig('fig_a', 'UMAP clusters')
    const result = makeResult('fig_a')
    const onAnnotate = vi.fn()
    render(<ResultView result={result} entities={[f]} onChange={() => {}} onFocus={() => {}}
                       onAnnotate={onAnnotate} />)
    const btn = document.querySelector('.hl-toggle') as HTMLButtonElement
    fireEvent.click(btn)
    const panel = document.querySelector('.rv-panel') as HTMLElement
    // Stub the panel's bounding rect so normXY gets sensible values.
    panel.getBoundingClientRect = () => ({
      x: 0, y: 0, top: 0, left: 0, bottom: 200, right: 300,
      width: 300, height: 200, toJSON: () => ({}),
    }) as DOMRect
    fireEvent.mouseEnter(panel)
    const surface = panel.querySelector('.rv-panel__hl') as HTMLElement
    expect(surface).not.toBeNull()
    fireEvent.mouseDown(surface, { clientX: 50, clientY: 60 })
    fireEvent.mouseMove(window, { clientX: 80, clientY: 90 })
    fireEvent.mouseMove(window, { clientX: 120, clientY: 110 })
    await act(async () => { fireEvent.mouseUp(window) })
    await waitFor(() => expect(onAnnotate).toHaveBeenCalled(), { timeout: 1000 })
    const arg = onAnnotate.mock.calls[0][0]
    expect(typeof arg.image).toBe('string')
    expect(arg.image.length).toBeGreaterThan(0)
    expect(typeof arg.note).toBe('string')
    expect(arg.note).toMatch(/User highlight \(this turn\)/)
    // Auto-exit: toggle returns to off
    await waitFor(() => {
      expect(btn.classList.contains('hl-toggle--on')).toBe(false)
    })
  })

  it('exits highlight mode when the focused result changes', () => {
    const f = fig('fig_a')
    const resA = makeResult('fig_a')
    const resB = { ...makeResult('fig_a'), id: 'res_h2', title: 'Different result' } as Entity
    const { rerender } = render(
      <ResultView result={resA} entities={[f]} onChange={() => {}} onFocus={() => {}}
                  onAnnotate={() => {}} />,
    )
    const btn = document.querySelector('.hl-toggle') as HTMLButtonElement
    fireEvent.click(btn)
    expect(btn.classList.contains('hl-toggle--on')).toBe(true)
    // Re-render with a different result entity (different id)
    rerender(<ResultView result={resB} entities={[f]} onChange={() => {}} onFocus={() => {}}
                         onAnnotate={() => {}} />)
    const btn2 = document.querySelector('.hl-toggle') as HTMLButtonElement
    expect(btn2.classList.contains('hl-toggle--on')).toBe(false)
  })
})
