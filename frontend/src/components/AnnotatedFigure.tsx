/**
 * Figure view with a spatial-reference overlay (Phase 25).
 *
 * Toggle "Mark", drag to draw an ellipse (or rectangle) over the figure,
 * then "Attach to chat" — the figure with the mark composited on is set as
 * a pending annotation. The next chat message sends it as an image so the
 * (vision) model can answer about the circled region.
 *
 * Coordinates are kept normalized (0–1) so the composite scales to the
 * image's natural resolution regardless of on-screen size.
 */
import { useRef, useState } from 'react'
import type { Entity } from '../types'

interface Annotation { image: string; note: string }

interface Props {
  entity: Entity
  onAttach: (a: Annotation) => void
}

type Shape = { kind: 'ellipse' | 'rect'; x0: number; y0: number; x1: number; y1: number }

export default function AnnotatedFigure({ entity, onAttach }: Props) {
  const [marking, setMarking] = useState(false)
  const [tool, setTool] = useState<'ellipse' | 'rect'>('ellipse')
  const [shape, setShape] = useState<Shape | null>(null)
  const [drawing, setDrawing] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement>(null)

  if (!entity.artifact_path) {
    return <p className="focus__placeholder">No artifact attached.</p>
  }

  function norm(e: React.MouseEvent): { x: number; y: number } {
    const rect = wrapRef.current!.getBoundingClientRect()
    return {
      x: Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
    }
  }

  function down(e: React.MouseEvent) {
    if (!marking) return
    const { x, y } = norm(e)
    setShape({ kind: tool, x0: x, y0: y, x1: x, y1: y })
    setDrawing(true)
  }
  function move(e: React.MouseEvent) {
    if (!drawing || !shape) return
    const { x, y } = norm(e)
    setShape({ ...shape, x1: x, y1: y })
  }
  function up() { setDrawing(false) }

  async function attach() {
    if (!shape || !imgRef.current) return
    const img = imgRef.current
    // Load at natural resolution for a crisp composite.
    const natural = new Image()
    natural.crossOrigin = 'anonymous'
    natural.src = img.src
    await natural.decode().catch(() => {})
    const W = natural.naturalWidth || img.width
    const H = natural.naturalHeight || img.height
    const canvas = document.createElement('canvas')
    canvas.width = W; canvas.height = H
    const ctx = canvas.getContext('2d')!
    ctx.drawImage(natural, 0, 0, W, H)
    ctx.strokeStyle = '#dc2626'
    ctx.lineWidth = Math.max(3, W / 250)
    const x = Math.min(shape.x0, shape.x1) * W
    const y = Math.min(shape.y0, shape.y1) * H
    const w = Math.abs(shape.x1 - shape.x0) * W
    const h = Math.abs(shape.y1 - shape.y0) * H
    if (shape.kind === 'ellipse') {
      ctx.beginPath()
      ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2)
      ctx.stroke()
    } else {
      ctx.strokeRect(x, y, w, h)
    }
    const dataUrl = canvas.toDataURL('image/png')
    const b64 = dataUrl.split(',')[1]
    const cx = ((shape.x0 + shape.x1) / 2).toFixed(2)
    const cy = ((shape.y0 + shape.y1) / 2).toFixed(2)
    onAttach({
      image: b64,
      note: `marked a ${shape.kind === 'ellipse' ? 'circled' : 'boxed'} region of "${entity.title}" near (${cx}, ${cy}) in normalized figure coords`,
    })
    setMarking(false)
  }

  const sx = shape ? Math.min(shape.x0, shape.x1) * 100 : 0
  const sy = shape ? Math.min(shape.y0, shape.y1) * 100 : 0
  const sw = shape ? Math.abs(shape.x1 - shape.x0) * 100 : 0
  const sh = shape ? Math.abs(shape.y1 - shape.y0) * 100 : 0

  return (
    <div className="annot">
      <div className="annot__bar">
        <button
          className={`annot__btn ${marking ? 'annot__btn--on' : ''}`}
          onClick={() => { setMarking(m => !m); if (marking) setShape(null) }}
        >
          ◎ {marking ? 'Marking…' : 'Mark region'}
        </button>
        {marking && (
          <>
            <button className={`annot__tool ${tool === 'ellipse' ? 'is-on' : ''}`} onClick={() => setTool('ellipse')}>circle</button>
            <button className={`annot__tool ${tool === 'rect' ? 'is-on' : ''}`} onClick={() => setTool('rect')}>box</button>
            {shape && <button className="annot__attach" onClick={attach}>Attach to chat →</button>}
            {shape && <button className="annot__tool" onClick={() => setShape(null)}>clear</button>}
          </>
        )}
      </div>
      <div
        className={`annot__wrap ${marking ? 'annot__wrap--marking' : ''}`}
        ref={wrapRef}
        onMouseDown={down} onMouseMove={move} onMouseUp={up} onMouseLeave={up}
      >
        <img ref={imgRef} className="focus__figure" src={entity.artifact_path} alt={entity.title} draggable={false} />
        {shape && (
          <svg className="annot__svg" viewBox="0 0 100 100" preserveAspectRatio="none">
            {shape.kind === 'ellipse' ? (
              <ellipse cx={sx + sw / 2} cy={sy + sh / 2} rx={sw / 2} ry={sh / 2}
                       fill="none" stroke="#dc2626" strokeWidth="0.6" vectorEffect="non-scaling-stroke" />
            ) : (
              <rect x={sx} y={sy} width={sw} height={sh}
                    fill="none" stroke="#dc2626" strokeWidth="0.6" vectorEffect="non-scaling-stroke" />
            )}
          </svg>
        )}
      </div>
    </div>
  )
}
