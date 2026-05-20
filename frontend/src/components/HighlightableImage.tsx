/**
 * An image you can highlight on: a compact highlighter toolbar overlaid
 * top-right, freehand-marker + box modes in thick fluorescent pink.
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
  /** Increment to erase the drawn mark (e.g. the chat chip was cleared). */
  clearSignal?: number
}

const PINK = 'rgba(236, 72, 153, 0.40)'
type Mode = 'highlight' | 'box'
type Pt = { x: number; y: number }

export default function HighlightableImage({ src, onAttach, label, hoverToolbar, className, clearSignal }: Props) {
  const [marking, setMarking] = useState(false)
  const [mode, setMode] = useState<Mode>('highlight')
  const [stroke, setStroke] = useState<Pt[]>([])
  const [box, setBox] = useState<Pt[] | null>(null)
  const [drawing, setDrawing] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement>(null)

  // The chat chip was cleared → erase the drawn mark and exit marking.
  useEffect(() => {
    if (clearSignal) { setStroke([]); setBox(null); setMarking(false) }
  }, [clearSignal])

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
    ctx.fillStyle = PINK; ctx.strokeStyle = PINK
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
    const what = label ? `the figure "${label}"` : 'the figure shown in the conversation'
    onAttach({
      image: b64,
      note: `The user highlighted a region of ${what} — marked in translucent pink on the attached image. Answer about the highlighted region specifically (they may refer to it as "here" or "the highlighted area").`,
    })
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
              ? <polyline points={strokePts} fill="none" stroke={PINK} strokeWidth="16"
                          strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
              : <rect x={bx} y={by} width={bw} height={bh} fill={PINK} />}
          </svg>
        )}
      </div>
      {/* Vertical badge gutter to the right of the figure — first action is
          the highlighter; more artifact actions can stack here later. */}
      <div className="annot__tb">
        <button
          className={`annot__tb-btn ${marking ? 'annot__tb-btn--on' : ''}`}
          onClick={() => { setMarking(m => !m); if (marking) clearMark() }}
          title="Highlight a region to ask Guide about it"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
            <path d="M15.6 2.6a2 2 0 012.8 0l3 3a2 2 0 010 2.8l-9 9-5.2 1.2 1.2-5.2 9-9zM5 19h14v2H5z"/>
          </svg>
        </button>
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
    </div>
  )
}
