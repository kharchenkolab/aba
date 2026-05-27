/**
 * TextCanvas — plain text viewer (no syntax). Used for .txt, .log, and
 * any synthesized text content.
 */
import { useEffect, useState } from 'react'
import type { ViewerComponentProps } from './types'
import './MarkdownCanvas.css'

export default function TextCanvas({ node }: ViewerComponentProps) {
  const inline = node.content ?? node.synthesized_content ?? null
  const [fetched, setFetched] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const text = inline ?? fetched ?? ''

  useEffect(() => {
    if (inline !== null && inline !== undefined) return
    if (!node.artifact_path) return
    let cancelled = false
    setError(null)
    fetch(node.artifact_path)
      .then(r => r.ok ? r.text() : Promise.reject(new Error(`${r.status}`)))
      .then(t => { if (!cancelled) setFetched(t) })
      .catch(e => { if (!cancelled) setError(String(e)) })
    return () => { cancelled = true }
  }, [inline, node.artifact_path])

  return (
    <article className="viewer viewer--text">
      <header className="viewer__head">
        <span className="viewer__path">{node.path}</span>
      </header>
      <div className="viewer__body">
        {error && <div className="viewer__error">Couldn't load: {error}</div>}
        {text && <pre className="code">{text}</pre>}
      </div>
    </article>
  )
}
