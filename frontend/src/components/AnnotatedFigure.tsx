/**
 * Figure view with a highlighter overlay (Phase 25, reworked).
 *
 * A highlighter-pen toggle turns on marking. Two modes:
 *   - highlight (freehand): paint a thick translucent-pink marker stroke
 *   - box: drag a translucent-pink rectangle
 * "Attach" composites the figure + highlight to a PNG and sets it as a
 * sticky annotation that rides along with chat messages until cleared —
 * so follow-up questions about the same region keep working.
 *
 * Coordinates are normalized (0–1) so the composite scales to the image's
 * natural resolution.
 */
import { useRef, useState } from 'react'
import type { Entity } from '../types'

interface Annotation { image: string; note: string }
interface Props { entity: Entity; onAttach: (a: Annotation) => void }

const PINK = 'rgba(236, 72, 153, 0.40)'   // fluorescent marker
type Mode = 'highlight' | 'box'
type Pt = { x: number; y: number }

export default function AnnotatedFigure({ entity, onAttach }: Props) {
  const [marking, setMarking] = useState(false)
  const [mode, setMode] = useState<Mode>('highlight')
  const [stroke, setStroke] = useState<Pt[]>([])     // freehand points
  const [box, setBox] = useState<Pt[] | null>(null)  // [start, end]
  const [drawing, setDrawing] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement>(null)

  if (!entity.artifact_path) {
    return <p className="focus__placeholder">No artifact attached.</p>
  }

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
    if (mode === 'highlight') setStroke([p])
    else setBox([p, p])
    setDrawing(true)
  }
  function move(e: React.MouseEvent) {
    if (!drawing) return
    const p = norm(e)
    if (mode === 'highlight') setStroke(s => [...s, p])
    else setBox(b => (b ? [b[0], p] : [p, p]))
  }
  function up() { setDrawing(false) }

  const hasMark = (mode === 'highlight' && stroke.length > 1) || (mode === 'box' && !!box)

  async function attach() {
    if (!hasMark || !imgRef.current) return
    const natural = new Image()
    natural.crossOrigin = 'anonymous'
    natural.src = imgRef.current.src
    await natural.decode().catch(() => {})
    const W = natural.naturalWidth || imgRef.current.width
    const H = natural.naturalHeight || imgRef.current.height
    const canvas = document.createElement('canvas')
    canvas.width = W; canvas.height = H
    const ctx = canvas.getContext('2d')!
    ctx.drawImage(natural, 0, 0, W, H)
    ctx.fillStyle = PINK
    ctx.strokeStyle = PINK
    if (mode === 'highlight') {
      ctx.lineWidth = Math.max(14, W / 28)
      ctx.lineCap = 'round'; ctx.lineJoin = 'round'
      ctx.beginPath()
      stroke.forEach((p, i) => (i ? ctx.lineTo(p.x * W, p.y * H) : ctx.moveTo(p.x * W, p.y * H)))
      ctx.stroke()
    } else if (box) {
      const x = Math.min(box[0].x, box[1].x) * W, y = Math.min(box[0].y, box[1].y) * H
      const w = Math.abs(box[1].x - box[0].x) * W, h = Math.abs(box[1].y - box[0].y) * H
      ctx.fillRect(x, y, w, h)
    }
    const b64 = canvas.toDataURL('image/png').split(',')[1]
    onAttach({
      image: b64,
      note: `The user highlighted a region of the figure "${entity.title}" — it is marked in translucent pink on the attached image. Answer about the highlighted region specifically.`,
    })
    setMarking(false)
  }

  // SVG overlay geometry (in 0–100 viewBox units)
  const strokePts = stroke.map(p => `${p.x * 100},${p.y * 100}`).join(' ')
  const bx = box ? Math.min(box[0].x, box[1].x) * 100 : 0
  const by = box ? Math.min(box[0].y, box[1].y) * 100 : 0
  const bw = box ? Math.abs(box[1].x - box[0].x) * 100 : 0
  const bh = box ? Math.abs(box[1].y - box[0].y) * 100 : 0

  return (
    <div className="annot">
      <div className="annot__bar">
        <button
          className={`annot__hl ${marking ? 'annot__hl--on' : ''}`}
          onClick={() => { setMarking(m => !m); if (marking) clearMark() }}
          title="Highlighter — mark a region to ask Guide about"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
            <path d="M15.6 2.6a2 2 0 012.8 0l3 3a2 2 0 010 2.8l-9 9-5.2 1.2 1.2-5.2 9-9zM5 19h14v2H5z"/>
          </svg>
          Highlight
        </button>
        {marking && (
          <>
            <span className="annot__sep" />
            <button className={`annot__tool ${mode === 'highlight' ? 'is-on' : ''}`}
                    onClick={() => { setMode('highlight'); clearMark() }}>marker</button>
            <button className={`annot__tool ${mode === 'box' ? 'is-on' : ''}`}
                    onClick={() => { setMode('box'); clearMark() }}>box</button>
            {hasMark && <button className="annot__attach" onClick={attach}>Attach to chat →</button>}
            {hasMark && <button className="annot__tool" onClick={clearMark}>clear</button>}
          </>
        )}
      </div>
      <div
        className={`annot__wrap ${marking ? 'annot__wrap--marking' : ''}`}
        ref={wrapRef}
        onMouseDown={down} onMouseMove={move} onMouseUp={up} onMouseLeave={up}
      >
        <img ref={imgRef} className="focus__figure" src={entity.artifact_path} alt={entity.title} draggable={false} />
        {hasMark && (
          <svg className="annot__svg" viewBox="0 0 100 100" preserveAspectRatio="none">
            {mode === 'highlight'
              ? <polyline points={strokePts} fill="none" stroke={PINK} strokeWidth="16"
                          strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
              : <rect x={bx} y={by} width={bw} height={bh} fill={PINK} />}
          </svg>
        )}
      </div>
    </div>
  )
}
