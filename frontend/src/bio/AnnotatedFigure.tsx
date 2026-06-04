/**
 * Figure view in the focus canvas. Thin wrapper over HighlightableImage —
 * the highlighter logic lives there so the focus canvas and chat figures
 * share one control.
 */
import type { Entity } from '../types'
import HighlightableImage from '../components/HighlightableImage'

interface Annotation { image: string; note: string }
interface Props { entity: Entity; onAttach: (a: Annotation) => void; clearSignal?: number }

export default function AnnotatedFigure({ entity, onAttach, clearSignal }: Props) {
  if (!entity.artifact_path) {
    return <p className="focus__placeholder">No artifact attached.</p>
  }
  return (
    <div className="annot">
      <HighlightableImage src={entity.artifact_path} label={entity.title} onAttach={onAttach} clearSignal={clearSignal} />
    </div>
  )
}
