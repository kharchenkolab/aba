/**
 * ImageCanvas — primary image viewer (canvas mode). Renders the image
 * centered in the central column. Cmd-click on the file row picks the
 * `image-modal` alternate for full-screen.
 */
import { useState } from 'react'
import type { ViewerComponentProps } from './types'
import './MarkdownCanvas.css'

export default function ImageCanvas({ node }: ViewerComponentProps) {
  const src = node.artifact_path || ''
  const [loadError, setLoadError] = useState(false)
  return (
    <article className="viewer viewer--image">
      <header className="viewer__head">
        <span className="viewer__path">{node.path}</span>
        {node.title && <span className="viewer__title">{node.title}</span>}
      </header>
      <div className="viewer__body" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
        {!src && <div className="viewer__empty">No image source.</div>}
        {src && !loadError && (
          <img src={src} alt={node.title || node.name}
               onError={() => setLoadError(true)}
               style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }} />
        )}
        {src && loadError && (
          <div className="viewer__error" style={{ maxWidth: 540, lineHeight: 1.5 }}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>Image failed to load.</div>
            <div style={{ fontSize: 12, fontFamily: 'var(--mono)', wordBreak: 'break-all' }}>{src}</div>
            <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--text-3)', fontStyle: 'italic' }}>
              The file is referenced by the entity graph but the bytes aren't at this path —
              the artifact may have been moved, deleted, or never written.
            </div>
            <div style={{ marginTop: 10 }}>
              <a href={src} target="_blank" rel="noreferrer">Open URL directly →</a>
            </div>
          </div>
        )}
      </div>
    </article>
  )
}
