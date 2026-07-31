/**
 * Highlight gesture on the focused Result.
 *
 * The Highlight TOGGLE lives in App.tsx's canvas-actions row (alongside
 * the Threads ✏️ button). ResultView only consumes the lifted
 * `highlighting` prop. So these tests drive `highlighting` directly via
 * props, no in-view button.
 *
 * Contract verified here:
 *   1. With highlighting=true, hovering a MemberPanel reveals the yellow
 *      surface overlay (.rv-panel__hl) with the "draw to highlight" hint.
 *   2. Drawing a stroke triggers captureHighlight, which calls
 *      onAnnotate with {image, note} and calls onHighlightingChange(false)
 *      (auto-exit).
 *   3. With highlighting=false, no surface appears even on hover.
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
  it('renders no in-view toggle button (lives in App.tsx canvas-actions)', () => {
    const f = fig('fig_a')
    const result = makeResult('fig_a')
    render(<ResultView result={result} entities={[f]} onChange={() => {}} onFocus={() => {}}
                       onAnnotate={() => {}} />)
    // The toggle is OWNED by App.tsx now, not ResultView. ResultView
    // should not render its own hl-toggle button.
    expect(document.querySelector('.hl-toggle')).toBeNull()
  })

  it('with highlighting=false, hover does NOT show surface', () => {
    const f = fig('fig_a')
    const result = makeResult('fig_a')
    render(<ResultView result={result} entities={[f]} onChange={() => {}} onFocus={() => {}}
                       onAnnotate={() => {}} highlighting={false} />)
    const panel = document.querySelector('.rv-panel') as HTMLElement
    fireEvent.mouseEnter(panel)
    expect(panel.querySelector('.rv-panel__hl')).toBeNull()
  })

  it('with highlighting=true, hovering a MemberPanel shows the yellow surface', () => {
    const f = fig('fig_a')
    const result = makeResult('fig_a')
    render(<ResultView result={result} entities={[f]} onChange={() => {}} onFocus={() => {}}
                       onAnnotate={() => {}} highlighting={true} />)
    const panel = document.querySelector('.rv-panel') as HTMLElement
    fireEvent.mouseEnter(panel)
    expect(panel.querySelector('.rv-panel__hl')).not.toBeNull()
    expect(panel.querySelector('.rv-panel__hl-hint')?.textContent).toContain('draw to highlight')
  })

  it('drawing a stroke calls onAnnotate({image, note}) and onHighlightingChange(false)', async () => {
    const f = fig('fig_a', 'UMAP clusters')
    const result = makeResult('fig_a')
    const onAnnotate = vi.fn()
    const onHighlightingChange = vi.fn()
    render(<ResultView result={result} entities={[f]} onChange={() => {}} onFocus={() => {}}
                       onAnnotate={onAnnotate}
                       highlighting={true}
                       onHighlightingChange={onHighlightingChange} />)
    const panel = document.querySelector('.rv-panel') as HTMLElement
    // Stub the panel's bounding rect so normXY works.
    const stubRect = () => ({
      x: 0, y: 0, top: 0, left: 0, bottom: 200, right: 300,
      width: 300, height: 200, toJSON: () => ({}),
    } as DOMRect)
    panel.getBoundingClientRect = stubRect
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
    // Auto-exit: parent's onHighlightingChange(false) called
    await waitFor(() => {
      expect(onHighlightingChange).toHaveBeenCalledWith(false)
    })
  })

  it('captured mark stays FROZEN while this panel owns hlOwner, and clears when ownership passes', async () => {
    const f = fig('fig_a', 'UMAP clusters')
    const result = makeResult('fig_a')
    const onAnnotate = vi.fn()
    const { rerender } = render(
      <ResultView result={result} entities={[f]} onChange={() => {}} onFocus={() => {}}
                  onAnnotate={onAnnotate} highlighting={true} />,
    )
    const panel = document.querySelector('.rv-panel') as HTMLElement
    panel.getBoundingClientRect = () => ({
      x: 0, y: 0, top: 0, left: 0, bottom: 200, right: 300,
      width: 300, height: 200, toJSON: () => ({}),
    } as DOMRect)
    fireEvent.mouseEnter(panel)
    const surface = panel.querySelector('.rv-panel__hl') as HTMLElement
    fireEvent.mouseDown(surface, { clientX: 50, clientY: 60 })
    fireEvent.mouseMove(window, { clientX: 80, clientY: 90 })
    fireEvent.mouseMove(window, { clientX: 120, clientY: 110 })
    await act(async () => { fireEvent.mouseUp(window) })
    await waitFor(() => expect(onAnnotate).toHaveBeenCalled(), { timeout: 1000 })

    // Contract: onAnnotate hands App a per-capture owner TOKEN as the 2nd arg.
    const token = onAnnotate.mock.calls[0][1]
    expect(typeof token).toBe('string')
    expect(token.length).toBeGreaterThan(0)

    // Before App claims ownership (hlOwner unset), no frozen overlay shows.
    expect(panel.querySelector('.rv-panel__hl--frozen')).toBeNull()

    // App pins this capture (hlOwner = token) and highlight mode exits: the
    // mark must now persist as a frozen overlay with NO "draw" hint.
    rerender(<ResultView result={result} entities={[f]} onChange={() => {}} onFocus={() => {}}
                         onAnnotate={onAnnotate} highlighting={false} hlOwner={token} />)
    const frozen = document.querySelector('.rv-panel__hl--frozen')
    expect(frozen).not.toBeNull()
    expect(frozen?.querySelector('polyline')).not.toBeNull()
    expect(document.querySelector('.rv-panel__hl-hint')).toBeNull()

    // Ownership passes to another highlight (or the chip is cleared → null):
    // this panel's frozen mark retires.
    rerender(<ResultView result={result} entities={[f]} onChange={() => {}} onFocus={() => {}}
                         onAnnotate={onAnnotate} highlighting={false} hlOwner={null} />)
    await waitFor(() => expect(document.querySelector('.rv-panel__hl--frozen')).toBeNull())
  })

  it('exits highlight mode when the focused result changes', () => {
    const f = fig('fig_a')
    const resA = makeResult('fig_a')
    const resB = { ...makeResult('fig_a'), id: 'res_h2', title: 'Different result' } as Entity
    const onHighlightingChange = vi.fn()
    const { rerender } = render(
      <ResultView result={resA} entities={[f]} onChange={() => {}} onFocus={() => {}}
                  onAnnotate={() => {}} highlighting={true}
                  onHighlightingChange={onHighlightingChange} />,
    )
    // Re-render with a different result entity (different id) — the
    // result-id effect should fire onHighlightingChange(false).
    rerender(<ResultView result={resB} entities={[f]} onChange={() => {}} onFocus={() => {}}
                         onAnnotate={() => {}} highlighting={true}
                         onHighlightingChange={onHighlightingChange} />)
    expect(onHighlightingChange).toHaveBeenCalledWith(false)
  })
})
