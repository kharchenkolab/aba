/**
 * TableViewer — first-pass CSV / TSV viewer.
 *
 * Reads the file in pages (200 lines at a time) via GET /api/files/raw
 * so a 10M-row table doesn't blow the browser. Renders the first row as
 * the header, the rest as cells. "Load more" pulls the next page.
 *
 * Future: column sort, sticky header, virtualized scroll, type sniffing.
 * This version is intentionally simple and the same plumbing
 * (/api/files/raw + lazy pagination) is reusable by other text-shaped
 * viewers (JSON, code, log).
 */
import { useEffect, useState } from 'react'
import type { ViewerComponentProps } from './types'
import './MarkdownCanvas.css'
import './TableViewer.css'

const PAGE = 200

function detectDelim(name: string, firstLine: string): string {
  if (name.toLowerCase().endsWith('.tsv')) return '\t'
  if (name.toLowerCase().endsWith('.csv')) return ','
  // Otherwise sniff: whichever appears more in the first line wins.
  const tabs = (firstLine.match(/\t/g) ?? []).length
  const commas = (firstLine.match(/,/g) ?? []).length
  return tabs > commas ? '\t' : ','
}

/** Naive CSV split that handles double-quote quoting + escaped quotes.
 *  Good enough for the preview; not a full RFC 4180 parser. */
function splitRow(line: string, delim: string): string[] {
  const out: string[] = []
  let cur = ''
  let inQ = false
  for (let i = 0; i < line.length; i++) {
    const c = line[i]
    if (inQ) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i += 1 }
      else if (c === '"') inQ = false
      else cur += c
    } else {
      if (c === '"') inQ = true
      else if (c === delim) { out.push(cur); cur = '' }
      else cur += c
    }
  }
  out.push(cur)
  return out
}

export default function TableViewer({ node }: ViewerComponentProps) {
  const [lines, setLines] = useState<string[]>([])
  const [nextOffset, setNextOffset] = useState(0)
  const [eof, setEof] = useState(false)
  const [truncated, setTruncated] = useState(false)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [totalSeen, setTotalSeen] = useState(0)

  async function fetchPage(offset: number) {
    setLoading(true); setErr(null)
    try {
      const r = await fetch(
        `/api/files/raw?path=${encodeURIComponent(node.path)}&offset=${offset}&max_lines=${PAGE}`,
      )
      const d = await r.json()
      if (!r.ok) throw new Error(d?.detail || `${r.status}`)
      setLines(prev => offset === 0 ? d.lines : [...prev, ...d.lines])
      setNextOffset(d.next_offset)
      setEof(d.eof)
      setTruncated(d.truncated)
      setTotalSeen(d.total_lines_seen)
    } catch (e) {
      setErr(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setLines([]); setNextOffset(0); setEof(false); setTruncated(false); setTotalSeen(0)
    fetchPage(0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.path])

  if (err) {
    return <article className="viewer"><div className="viewer__error" style={{ padding: 16 }}>Couldn't read file: {err}</div></article>
  }

  const delim = lines.length ? detectDelim(node.name, lines[0]) : ','
  const parsedRows = lines.map(l => splitRow(l, delim))

  // Heuristic: row 1 is a header if it has fewer numeric cells than row 2
  // (e.g. row 1 has column names, row 2 has the first data row).
  // For a single-line file, treat it as data (no header).
  const looksLikeHeader = (() => {
    if (parsedRows.length < 2) return false
    const numericCount = (cells: string[]) => cells.filter(c => {
      const t = c.trim()
      return t !== '' && !Number.isNaN(Number(t))
    }).length
    const n1 = numericCount(parsedRows[0])
    const n2 = numericCount(parsedRows[1])
    // strong signal: row 1 all-strings while row 2 has any numerics
    if (n1 === 0 && n2 > 0) return true
    // softer: row 2 has noticeably more numerics
    return n2 > n1 + 1
  })()

  const header: string[] | null = looksLikeHeader ? parsedRows[0] : null
  const rows = looksLikeHeader ? parsedRows.slice(1) : parsedRows
  const ncols = parsedRows[0]?.length ?? 0

  return (
    <article className="viewer viewer--table">
      <header className="viewer__head">
        <span className="viewer__path">{node.path}</span>
        <span className="viewer__meta">
          {lines.length > 0
            ? `${rows.length} row${rows.length === 1 ? '' : 's'}${header ? ` · ${ncols} cols` : ' · no header'}`
            : 'loading…'}
          {eof && lines.length > 0 ? ' · end of file' : ''}
          {truncated ? ' · row truncated' : ''}
        </span>
      </header>
      <div className="viewer__body table-body">
        {lines.length === 0 && loading && <div className="viewer__empty">Loading…</div>}
        {lines.length > 0 && (
          <table className="table-viewer">
            {header && (
              <thead>
                <tr>{header.map((h, i) => <th key={i}>{h || `col${i + 1}`}</th>)}</tr>
              </thead>
            )}
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>
                  {r.map((c, ci) => <td key={ci}>{c}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!eof && lines.length > 0 && (
          <div className="table-viewer__more">
            <button
              className="viewer__action"
              onClick={() => fetchPage(nextOffset)}
              disabled={loading}
            >
              {loading ? 'Loading…' : `Load next ${PAGE} rows`}
            </button>
            <span style={{ marginLeft: 12, color: 'var(--text-4)', fontSize: 12 }}>
              {totalSeen} rows scanned so far
            </span>
          </div>
        )}
      </div>
    </article>
  )
}
