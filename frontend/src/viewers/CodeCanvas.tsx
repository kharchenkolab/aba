/**
 * CodeCanvas — syntax-naive code viewer (V1). Renders preformatted text
 * with the file's extension hinted as a CSS class for future syntax
 * highlighting (Prism / Shiki integration is a V2 follow-up).
 */
import { useEffect, useState } from 'react'
import type { ViewerComponentProps } from './types'
import './MarkdownCanvas.css'

function extOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1).toLowerCase() : ''
}

export default function CodeCanvas({ node }: ViewerComponentProps) {
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

  const lang = extOf(node.name)

  return (
    <article className="viewer viewer--code">
      <header className="viewer__head">
        <span className="viewer__path">{node.path}</span>
        {lang && <span className="viewer__lang">.{lang}</span>}
      </header>
      <div className="viewer__body">
        {error && <div className="viewer__error">Couldn't load: {error}</div>}
        {text && <pre className={`code code--${lang}`}><code>{text}</code></pre>}
      </div>
    </article>
  )
}
