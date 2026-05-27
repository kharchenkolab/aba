/**
 * ImageCanvas — primary image viewer (canvas mode). Renders the image
 * centered in the central column. Cmd-click on the file row picks the
 * `image-modal` alternate for full-screen.
 */
import type { ViewerComponentProps } from './types'
import './MarkdownCanvas.css'

export default function ImageCanvas({ node }: ViewerComponentProps) {
  const src = node.artifact_path || ''
  return (
    <article className="viewer viewer--image">
      <header className="viewer__head">
        <span className="viewer__path">{node.path}</span>
        {node.title && <span className="viewer__title">{node.title}</span>}
      </header>
      <div className="viewer__body" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
        {src ? (
          <img src={src} alt={node.title || node.name}
               style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }} />
        ) : (
          <div className="viewer__empty">No image source.</div>
        )}
      </div>
    </article>
  )
}
