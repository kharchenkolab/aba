/**
 * An image you can highlight on: a compact highlighter toolbar overlaid
 * top-right, freehand-marker + box modes in thick highlighter yellow.
 * Finishing a mark auto-attaches the composited (downsampled) image via
 * onAttach — no entity lookup needed; the conversation already has the
 * context for whatever was just plotted.
 *
 * Used both in the focus canvas (AnnotatedFigure) and on chat figures.
 */
import { useEffect, useRef, useState } from 'react'

interface Annotation { image: string; note: string }
interface Props {
  src: string
  onAttach: (a: Annotation) => void
  /** Short label for the note (e.g. figure title); optional. */
  label?: string
  /** Hide the toolbar until the image is hovered (for chat figures). */
  hoverToolbar?: boolean
  className?: string
  /** Increment to erase the drawn mark AND exit marking (chat chip cleared). */
  clearSignal?: number
  /** Increment to erase just the drawn mark, staying in marking mode. */
  clearMarkSignal?: number
  /** Controlled marking mode — when provided, an external button owns the
   * on/off toggle (e.g. a modal header), so we hide our own primary toggle. */
  marking?: boolean
  onMarkingChange?: (m: boolean) => void
  /** Controlled draw mode (freehand vs box). */
  mode?: Mode
  onModeChange?: (m: Mode) => void
  /** Show the built-in primary highlighter toggle (default true). */
  showToggle?: boolean
  /** Render no toolbar gutter at all — the host supplies the controls. */
  hideToolbar?: boolean
}

const HILITE = 'rgba(253, 224, 71, 0.55)'   // highlighter yellow
type Mode = 'highlight' | 'box'
type Pt = { x: number; y: number }

export default function HighlightableImage({ src, onAttach, label, hoverToolbar, className, clearSignal, clearMarkSignal, marking: markingProp, onMarkingChange, mode: modeProp, onModeChange, showToggle = true, hideToolbar }: Props) {
  const [markingState, setMarkingState] = useState(false)
  const marking = markingProp ?? markingState
  const setMarking = (v: boolean) => { onMarkingChange ? onMarkingChange(v) : setMarkingState(v) }
  const [modeState, setModeState] = useState<Mode>('highlight')
  const mode = modeProp ?? modeState
  const setMode = (m: Mode) => { onModeChange ? onModeChange(m) : setModeState(m) }
  const [stroke, setStroke] = useState<Pt[]>([])
  const [box, setBox] = useState<Pt[] | null>(null)
  const [drawing, setDrawing] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement>(null)

  // The chat chip was cleared → erase the drawn mark and exit marking.
  useEffect(() => {
    if (clearSignal) { setStroke([]); setBox(null); setMarking(false) }
  }, [clearSignal])

  // Erase just the drawn mark (host's clear / mode-switch), staying in marking.
  useEffect(() => {
    if (clearMarkSignal) { setStroke([]); setBox(null) }
  }, [clearMarkSignal])

  function norm(e: React.MouseEvent): Pt {
    const r = wrapRef.current!.getBoundingClientRect()
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    }
  }
  function clearMark() { setStroke([]); setBox(null) }
  function down(e: React.MouseEvent) {
    if (!marking) return
    const p = norm(e)
    if (mode === 'highlight') setStroke([p]); else setBox([p, p])
    setDrawing(true)
  }
  function moveE(e: React.MouseEvent) {
    if (!drawing) return
    const p = norm(e)
    if (mode === 'highlight') setStroke(s => [...s, p]); else setBox(b => (b ? [b[0], p] : [p, p]))
  }
  function up() {
    if (!drawing) return   // ignore mouseleave when not actively drawing (avoids a duplicate attach)
    setDrawing(false)
    if ((mode === 'highlight' && stroke.length > 1) || (mode === 'box' && !!box)) attach()
  }

  const hasMark = (mode === 'highlight' && stroke.length > 1) || (mode === 'box' && !!box)

  async function attach() {
    if (!imgRef.current) return
    const natural = new Image()
    natural.crossOrigin = 'anonymous'
    natural.src = imgRef.current.src
    await natural.decode().catch(() => {})
    const nW = natural.naturalWidth || imgRef.current.width
    const nH = natural.naturalHeight || imgRef.current.height
    const scale = nW > 512 ? 512 / nW : 1
    const W = Math.round(nW * scale), H = Math.round(nH * scale)
    const canvas = document.createElement('canvas')
    canvas.width = W; canvas.height = H
    const ctx = canvas.getContext('2d')!
    ctx.drawImage(natural, 0, 0, W, H)
    ctx.fillStyle = HILITE; ctx.strokeStyle = HILITE
    if (mode === 'highlight') {
      ctx.lineWidth = Math.max(12, W / 28); ctx.lineCap = 'round'; ctx.lineJoin = 'round'
      ctx.beginPath()
      stroke.forEach((p, i) => (i ? ctx.lineTo(p.x * W, p.y * H) : ctx.moveTo(p.x * W, p.y * H)))
      ctx.stroke()
    } else if (box) {
      const x = Math.min(box[0].x, box[1].x) * W, y = Math.min(box[0].y, box[1].y) * H
      const w = Math.abs(box[1].x - box[0].x) * W, h = Math.abs(box[1].y - box[0].y) * H
      ctx.fillRect(x, y, w, h)
    }
    const b64 = canvas.toDataURL('image/png').split(',')[1]
    const what = label ? `the figure "${label}"` : 'the figure'
    // First-person, mode-aware note. Reads as the user speaking, so the
    // model treats it as a directive, not metadata. Mentions the exact
    // shape (stroke vs box) so the model knows what to look for.
    const shape =
      mode === 'highlight'
        ? 'a yellow freehand mark (likely a circle, line, or squiggle)'
        : 'a yellow box'
    const note =
      `I drew ${shape} on ${what} in the attached image to point at a specific region — ` +
      `focus your answer there. The mark is translucent yellow; the underlying figure shows through. ` +
      `I may refer to that region as "here" or "the highlighted area".`
    onAttach({ image: b64, note })
    // Focus is now established (the red chip appears) — leave marking mode. The
    // drawn mark stays on the figure until the chip is cleared (clearSignal).
    setMarking(false)
  }

  const strokePts = stroke.map(p => `${p.x * 100},${p.y * 100}`).join(' ')
  const bx = box ? Math.min(box[0].x, box[1].x) * 100 : 0
  const by = box ? Math.min(box[0].y, box[1].y) * 100 : 0
  const bw = box ? Math.abs(box[1].x - box[0].x) * 100 : 0
  const bh = box ? Math.abs(box[1].y - box[0].y) * 100 : 0

  return (
    <div className={`annot__row ${hoverToolbar ? 'annot__row--hover' : ''}`}>
      <div
        className={`annot__wrap ${marking ? 'annot__wrap--marking' : ''}`}
        ref={wrapRef}
        onMouseDown={down} onMouseMove={moveE} onMouseUp={up} onMouseLeave={up}
      >
        <img ref={imgRef} className={className ?? 'focus__figure'} src={src} alt={label ?? 'figure'} draggable={false} />
        {hasMark && (
          <svg className="annot__svg" viewBox="0 0 100 100" preserveAspectRatio="none">
            {mode === 'highlight'
              ? <polyline points={strokePts} fill="none" stroke={HILITE} strokeWidth="16"
                          strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
              : <rect x={bx} y={by} width={bw} height={bh} fill={HILITE} />}
          </svg>
        )}
      </div>
      {/* Vertical badge gutter to the right of the figure — first action is
          the highlighter; more artifact actions can stack here later. */}
      {!hideToolbar && (showToggle || marking) && (
      <div className="annot__tb">
        {showToggle && (
        <button
          className={`annot__tb-btn ${marking ? 'annot__tb-btn--on' : ''}`}
          onClick={() => { if (marking) clearMark(); setMarking(!marking) }}
          title="Highlight a region to ask Guide about it"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="#fde047" stroke="#ca8a04" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M15.6 2.6a2 2 0 012.8 0l3 3a2 2 0 010 2.8l-9 9-5.2 1.2 1.2-5.2 9-9zM5 19h14v2H5z"/>
          </svg>
        </button>
        )}
        {marking && (
          <>
            <button className={`annot__tb-btn ${mode === 'highlight' ? 'annot__tb-btn--sel' : ''}`}
                    title="Freehand marker" onClick={() => { setMode('highlight'); clearMark() }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M4 18 Q9 6 14 13 T20 8" /></svg>
            </button>
            <button className={`annot__tb-btn ${mode === 'box' ? 'annot__tb-btn--sel' : ''}`}
                    title="Box" onClick={() => { setMode('box'); clearMark() }}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4"><rect x="4" y="6" width="16" height="12" rx="1.5" /></svg>
            </button>
            {hasMark && (
              <button className="annot__tb-btn" title="Clear mark" onClick={clearMark}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
              </button>
            )}
          </>
        )}
      </div>
      )}
    </div>
  )
}
