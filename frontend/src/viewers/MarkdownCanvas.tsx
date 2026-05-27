/**
 * MarkdownCanvas — renders a .md file (entity-backed or synthesized)
 * as proper Markdown in the central column.
 *
 * For synthesized files (READMEs, claim .md, etc.) the content lives
 * in node.content / node.synthesized_content. For real .md artifacts
 * we'd need to fetch via /api/files/download; the V1 release covers
 * synthesized files only (the visible-bug fix) and falls back to a
 * fetch when artifact_path is present.
 */
import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ViewerComponentProps } from './types'
import './MarkdownCanvas.css'

export default function MarkdownCanvas({ node }: ViewerComponentProps) {
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
    <article className="viewer viewer--markdown">
      <header className="viewer__head">
        <span className="viewer__path">{node.path}</span>
      </header>
      <div className="viewer__body markdown-body">
        {error && <div className="viewer__error">Couldn't load: {error}</div>}
        {!text && !error && <div className="viewer__empty">No content.</div>}
        {text && <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>}
      </div>
    </article>
  )
}
