import { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { DisplayMessage, Block, Entity } from '../types'
import { AgentAvatar } from './icons'
import './Message.css'

interface Annotation { image: string; note: string }
const HILITE = 'rgba(253, 224, 71, 0.55)'   // highlighter yellow
type Pt = { x: number; y: number }

// Concrete shape + position descriptor for a freehand highlight stroke —
// used in the agent-facing note so the model has explicit language ("upper-
// right quadrant", "closed loop", "covers ~12% of the figure") instead of
// only seeing "user marked something" abstractly. All math on normalized
// (0–1) coords; no DOM access needed.
function describeStroke(pts: Pt[]): string {
  if (pts.length < 2) return 'a small mark'
  const xs = pts.map(p => p.x), ys = pts.map(p => p.y)
  const xmin = Math.min(...xs), xmax = Math.max(...xs)
  const ymin = Math.min(...ys), ymax = Math.max(...ys)
  const w = xmax - xmin, h = ymax - ymin
  const cx = (xmin + xmax) / 2, cy = (ymin + ymax) / 2
  // Closed loop heuristic: endpoint distance vs total path length.
  let pathLen = 0
  for (let i = 1; i < pts.length; i++) {
    pathLen += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y)
  }
  const endGap = Math.hypot(pts[0].x - pts[pts.length - 1].x,
                            pts[0].y - pts[pts.length - 1].y)
  const closed = pathLen > 0.1 && endGap / pathLen < 0.15
  const shape =
    closed                            ? 'a closed loop circling'
    : (h < 0.05 && w > 0.1)           ? 'a horizontal underline across'
    : (w < 0.05 && h > 0.1)           ? 'a vertical mark down'
    : (pathLen < 0.15)                ? 'a small mark on'
                                      : 'an open stroke across'
  // Position by bbox center → 3×3 grid label.
  const col = cx < 0.33 ? 'left' : cx < 0.67 ? 'center' : 'right'
  const row = cy < 0.33 ? 'top'  : cy < 0.67 ? 'middle' : 'bottom'
  const quadrant =
    (row === 'middle' && col === 'center') ? 'the center'
    : (row === 'middle')                   ? `the ${col} side`
    : (col === 'center')                   ? `the ${row} edge`
                                           : `the ${row}-${col} region`
  // Size as percent of cell area (bbox area, integer percent, lower bound 1).
  const areaPct = Math.max(1, Math.round(w * h * 100))
  return `${shape} ${quadrant} of the figure (the marked region covers ~${areaPct}% of the cell area)`
}

// If the highlighted cell contains an <img> tied to a figure entity, return
// a short reference for the agent ("The figure is 'GSM5746260: UMAP by Cluster'
// (fig_abc12)"). Falls back to empty string when the entity isn't known —
// caller appends its own fallback descriptor.
function describeHighlightedFigure(cellEl: HTMLElement | null,
                                   entities: Entity[] | undefined): string {
  if (!cellEl || !entities || entities.length === 0) return ''
  const imgs = cellEl.querySelectorAll('img[src]')
  for (const img of Array.from(imgs)) {
    const src = (img as HTMLImageElement).getAttribute('src') || ''
    // Match by artifact URL — figures register with artifact_path =
    // /artifacts/<pid>/<hash>.png; img.src is the same.
    const hit = entities.find(e =>
      e.type === 'figure' && typeof e.artifact_path === 'string' && src.endsWith(e.artifact_path))
    if (hit) return `The marked figure is "${hit.title}" (${hit.id}).`
  }
  return ''
}

/** A failed turn: error headline, a small retry icon on the right, and an
 *  expandable disclosure for the raw error detail. */
function ErrorLine({ text, detail, onRetry }: { text: string; detail?: string; onRetry?: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="msg-error">
      <div className="msg-error__row">
        <svg className="msg-error__icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 3L22 20H2L12 3z" /><path d="M12 10v4" /><circle cx="12" cy="17.5" r="0.6" fill="currentColor" stroke="none" />
        </svg>
        <span className="msg-error__text">{text}</span>
        {detail && (
          <button className="msg-error__toggle" onClick={() => setOpen(o => !o)}>
            {open ? 'Hide details' : 'Details'}
          </button>
        )}
        {onRetry && (
          <button className="msg-error__retry" onClick={onRetry} title="Retry" aria-label="Retry">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12a9 9 0 1 1-2.6-6.4M21 4v5h-5" />
            </svg>
          </button>
        )}
      </div>
      {open && detail && <pre className="msg-error__detail">{detail}</pre>}
    </div>
  )
}

// Human-friendly, lower-case tool labels for the chat (biologists shouldn't see
// raw function names like `ensure_capability`). Anything unmapped falls back to a
// de-underscored phrase rather than snake_case.
function toolDoneLabel(name: string) {
  const labels: Record<string, string> = {
    list_data_files: 'listed data files',
    read_csv_info: 'read CSV',
    run_python: 'ran Python',
    run_r: 'ran R',
    run_nextflow: 'ran pipeline',
    ensure_capability: 'tools ready',
    list_capabilities: 'checked available tools',
    read_capability: 'read tool details',
    inspect_package: 'inspected package',
    search_pypi: 'searched packages',
    search_bioconda: 'searched bioconda',
    search_nf_core: 'searched pipelines',
    search_mcp_registry: 'searched tool servers',
    propose_capability: 'added a tool',
    search_skills: 'looked up methods',
    read_skill: 'read the recipe',
    open_run: 'started a run',
    close_run: 'closed the run',
    register_dataset: 'registered dataset',
    pin_entity: 'pinned',
    promote_to_result: 'saved result',
    create_finding: 'recorded finding',
    create_claim: 'recorded claim',
    annotate_entity: 'updated entity',
    list_entities: 'listed items',
    read_memory: 'recalled a note',
    write_memory: 'saved a note',
    fetch_url: 'fetched data',
    fetch_ensembl: 'queried Ensembl',
    lookup_sra_runinfo: 'looked up SRA runs',
    register_reference: 'registered reference',
    find_reference: 'found reference',
    restart_kernel: 'restarted session',
    get_provenance: 'traced provenance',
    get_dependents: 'found dependents',
    create_scenario: 'created scenario',
  }
  return labels[name] ?? name.replace(/_/g, ' ')
}
function toolRunningLabel(name: string) {
  const labels: Record<string, string> = {
    list_data_files: 'listing data files',
    read_csv_info: 'reading CSV',
    run_python: 'running Python',
    run_r: 'running R',
    run_nextflow: 'running pipeline',
    ensure_capability: 'setting up tools',
    list_capabilities: 'checking available tools',
    read_capability: 'reading tool details',
    inspect_package: 'inspecting package',
    search_pypi: 'searching packages',
    search_bioconda: 'searching bioconda',
    search_nf_core: 'searching pipelines',
    search_mcp_registry: 'searching tool servers',
    propose_capability: 'adding a tool',
    search_skills: 'looking up methods',
    read_skill: 'reading the recipe',
    open_run: 'starting a run',
    close_run: 'closing the run',
    register_dataset: 'registering dataset',
    pin_entity: 'pinning',
    promote_to_result: 'saving result',
    create_finding: 'recording finding',
    create_claim: 'recording claim',
    annotate_entity: 'updating entity',
    list_entities: 'listing items',
    read_memory: 'recalling a note',
    write_memory: 'saving a note',
    fetch_url: 'fetching data',
    fetch_ensembl: 'querying Ensembl',
    lookup_sra_runinfo: 'looking up SRA runs',
    register_reference: 'registering reference',
    find_reference: 'finding reference',
    restart_kernel: 'restarting session',
    get_provenance: 'tracing provenance',
    get_dependents: 'finding dependents',
    create_scenario: 'creating scenario',
  }
  return labels[name] ?? name.replace(/_/g, ' ')
}

/**
 * Render a message's blocks, merging each tool_start with its following
 * tool_result into a single indicator that resolves spinner → green check.
 * Tool indicators are hidden when `collapseTools` is set (older messages).
 */
/** Per-figure pin (a cell can hold several plots — each is its own entity).
 *  Icon-only, hover-reveal in the plot's top-right corner — same style as the
 *  message toolbar buttons. */
function FigurePin({ entity, isPinned, onPin }: {
  entity: Entity
  isPinned: boolean
  onPin: (id: string, pinned: boolean) => void
}) {
  // Optimistic flip on click for instant feedback; reconciles with the
  // authoritative `isPinned` (derived from active Results that include
  // this figure) once the server confirms. Resets when isPinned changes.
  const [opt, setOpt] = useState<boolean | null>(null)
  useEffect(() => { setOpt(null) }, [isPinned])
  const pinned = opt ?? isPinned
  return (
    <button
      className={`msg__tool msg__tool--pin ${pinned ? 'msg__tool--pinned' : 'msg__tool--hover'}`}
      onClick={() => { setOpt(!pinned); onPin(entity.id, !pinned) }}
      title={pinned ? 'Pinned — click to unpin' : 'Pin this figure'}
    >
      <svg viewBox="0 0 24 24" fill={pinned ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2"><path d="M12 17v5M9 3h6l-1 7 3 3H7l3-3z"/></svg>
    </button>
  )
}

function PlanCard({ block, active, onGo, onAdjust }: {
  block: Extract<Block, { type: 'plan' }>
  active: boolean; onGo?: (saveAsRun: boolean) => void; onAdjust?: () => void
}) {
  // Pre-checked: by default a plan's outputs group into one Run (see open_run).
  // Unchecking rides along on the Go message as a hint to skip it.
  const [saveAsRun, setSaveAsRun] = useState(true)
  // T2.5: steps can be strings (legacy) or PlanStepShape objects with
  // title/description/expected_outputs/skill/parameters.
  const steps = (Array.isArray(block.steps) ? block.steps : []) as (
    string | { n?: number; title: string; description?: string; expected_outputs?: string[]; skill?: string | null; parameters?: Record<string, unknown> }
  )[]
  // The model occasionally emits non-array shapes (string, object, null) — normalize
  // at ingestion so a bad payload doesn't crash the message and white-screen the chat.
  // See [[feedback_render_robustness]] in memory.
  const assumptions: string[] = Array.isArray(block.assumptions)
    ? (block.assumptions as unknown[]).map(a => typeof a === 'string' ? a : JSON.stringify(a))
    : []
  const concerns = (Array.isArray(block.concerns) ? block.concerns : []) as { step_n?: number | null; text?: string }[]
  const planConcerns = concerns.filter(c => c && c.step_n == null)
  const stepConcerns = (n: number) => concerns.filter(c => c && c.step_n === n)
  return (
    <div className="plan-card">
      <div className="plan-card__head">
        <span className="plan-card__spark">✦</span>{block.title || 'Plan'}
      </div>
      {block.summary && <div className="plan-card__summary">{block.summary}</div>}
      {block.rationale && <div className="plan-card__why">{block.rationale}</div>}
      {assumptions.length > 0 && (
        <div className="plan-card__assumptions">
          <div className="plan-card__assumptions-label">Assumptions</div>
          <ul>{assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </div>
      )}
      <ol className="plan-card__steps">
        {steps.map((s, i) => {
          const n = typeof s === 'object' && s.n ? s.n : i + 1
          const title = typeof s === 'string' ? s : s.title
          const desc = typeof s === 'object' ? s.description : undefined
          const outs = typeof s === 'object' ? s.expected_outputs : undefined
          const skill = typeof s === 'object' ? s.skill : undefined
          const myConcerns = stepConcerns(n)
          return (
            <li key={i} className="plan-card__step">
              <div className="plan-card__step-title">
                {title}
                {skill && <span className="plan-card__skill" title="reusable skill">{skill}</span>}
              </div>
              {desc && <div className="plan-card__step-desc">{desc}</div>}
              {outs && outs.length > 0 && (
                <div className="plan-card__outs">
                  → {outs.join(', ')}
                </div>
              )}
              {myConcerns.map((c, j) => (
                <div key={j} className={`plan-card__concern plan-card__concern--${c.level}`}>
                  {c.level === 'warn' ? '⚠' : c.level === 'error' ? '⛔' : '·'} {c.message}
                </div>
              ))}
            </li>
          )
        })}
      </ol>
      {planConcerns.map((c, j) => (
        <div key={j} className={`plan-card__concern plan-card__concern--${c.level}`}>
          {c.level === 'warn' ? '⚠' : c.level === 'error' ? '⛔' : '·'} {c.message}
        </div>
      ))}
      {active && (
        <div className="plan-card__actions">
          <button className="plan-card__go" onClick={() => onGo?.(saveAsRun)}>Go</button>
          <button className="plan-card__adjust" onClick={onAdjust}>Adjust…</button>
          <label className="plan-card__saverun"
                 title="Group this plan's outputs into one Run in the project tree">
            <input type="checkbox" checked={saveAsRun}
                   onChange={e => setSaveAsRun(e.target.checked)} />
            Save as a run
          </label>
        </div>
      )}
    </div>
  )
}

function renderBlocks(blocks: Block[], collapseTools: boolean, onRetry?: () => void, entities?: Entity[], onPin?: (id: string, pinned: boolean) => void,
                      planActive?: boolean, onPlanGo?: (saveAsRun: boolean) => void, onPlanAdjust?: () => void,
                      isStreaming?: boolean, pinnedFigureIds?: Set<string>,
                      fileMap?: Map<string, { url: string; kind: 'plot' | 'table' | 'file' }>) {
  const out: React.ReactNode[] = []
  // Override inline `code` so basenames the agent quotes resolve to a link
  // (only when the basename actually corresponds to a file written this thread).
  // Bare code (variable names, identifiers) renders unchanged.
  const mdComponents = fileMap && fileMap.size > 0 ? {
    code: (props: { inline?: boolean; children?: React.ReactNode; className?: string }) => {
      const raw = String(props.children ?? '').trim()
      const hit = !props.className /* not a fenced block */ && fileMap.get(raw)
      if (!hit) return <code className={props.className}>{props.children}</code>
      const title = `Open ${raw}`
      return (
        <a className={`msg-filelink msg-filelink--${hit.kind}`}
           href={hit.url} target="_blank" rel="noreferrer" title={title}>
          <code>{props.children}</code>
        </a>
      )
    },
  } : undefined
  for (let i = 0; i < blocks.length; i++) {
    const b = blocks[i]
    if (b.type === 'plan') {
      out.push(<PlanCard key={i} block={b} active={!!planActive} onGo={onPlanGo} onAdjust={onPlanAdjust} />)
    } else if (b.type === 'text') {
      out.push(
        <div key={i} className="msg-text">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>{b.text}</ReactMarkdown>
        </div>,
      )
    } else if (b.type === 'error') {
      out.push(<ErrorLine key={i} text={b.text} detail={b.detail} onRetry={onRetry} />)
    } else if (b.type === 'notice') {
      // Spinner only while the message is still streaming — terminal
      // notices like '(cancelled)' that get committed after streaming
      // ends shouldn't keep spinning.
      out.push(
        <div key={i} className="msg-notice">
          {isStreaming && <span className="tool-spinner" />}
          <span>{b.text}</span>
        </div>,
      )
    } else if (b.type === 'image') {
      // Title from the registered figure/table entity, if any. (Highlight + pin
      // now live in the per-message toolbar and act on the whole cell.)
      const ent = entities?.find(e => e.artifact_path === b.url && (e.type === 'figure' || e.type === 'table'))
      out.push(
        <div key={i} className="msg-image">
          {ent && <div className="msg-image__head"><span className="msg-image__title">{ent.title}</span></div>}
          <div className="msg-image__frame">
            <ZoomableImg src={b.url} alt={b.alt ?? 'plot'} />
            {ent && onPin && (
              <div className="msg-image__tools">
                <FigurePin entity={ent} isPinned={!!pinnedFigureIds?.has(ent.id)} onPin={onPin} />
              </div>
            )}
          </div>
        </div>,
      )
    } else if (b.type === 'tool_start') {
      if (collapseTools) continue
      // Look ahead for a matching tool_result.
      const result = blocks
        .slice(i + 1)
        .find(x => x.type === 'tool_result') as Extract<Block, { type: 'tool_result' }> | undefined
      out.push(<ToolLine key={i} block={b} result={result} />)
    } else if (b.type === 'tool_result') {
      // Rendered together with its tool_start above; skip.
      continue
    }
  }
  return out
}

// A single tool step. For tools that ran a script (run_python), a "Show script"
// disclosure reveals the exact code — the inner-loop detail the Trace panel used
// A chat plot that opens to a full-viewport lightbox on click — the chat column
// downsizes the (≈150 DPI) PNG, so click-to-zoom surfaces the native resolution.
function ZoomableImg({ src, alt }: { src: string; alt: string }) {
  const [open, setOpen] = useState(false)
  // crossOrigin enables highlight-to-canvas, but if the load fails for any
  // reason, retry WITHOUT it (an image is more useful than blank space — the
  // Run-view img has no crossOrigin and always renders). Reset per src.
  const [cors, setCors] = useState(true)
  useEffect(() => { setCors(true) }, [src])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])
  return (
    <>
      <img className="msg-image__img" src={src} alt={alt}
        crossOrigin={cors ? 'anonymous' : undefined}
        onError={() => { if (cors) setCors(false) }}
        style={{ cursor: 'zoom-in' }} title="Click to view full size"
        onClick={() => setOpen(true)} />
      {open && (
        <div className="lightbox" role="dialog" aria-modal="true" onClick={() => setOpen(false)}>
          <img className="lightbox__img" src={src} alt={alt}
            crossOrigin={cors ? 'anonymous' : undefined}
            onClick={e => e.stopPropagation()} />
          <button className="lightbox__close" onClick={() => setOpen(false)} aria-label="Close">×</button>
        </div>
      )}
    </>
  )
}

// to carry, now available per cell.
function ToolLine({ block, result }: {
  block: Extract<Block, { type: 'tool_start' }>
  result?: Extract<Block, { type: 'tool_result' }>
}) {
  const [showCode, setShowCode] = useState(false)
  const [showOut, setShowOut] = useState(false)
  const done = !!result
  const hasError = done && 'error' in result!.result
  const code = typeof block.input?.code === 'string' ? (block.input.code as string) : ''
  // Build a textual rendering of the tool's stdout/stderr (post-2026-05-31:
  // chat-side counterpart to "script" — peek at what the cell actually printed).
  // Structured fields (plots, tables) render elsewhere in the chat already;
  // here we surface the raw text streams + the error blob when there is one.
  const out: string = (() => {
    if (!result) return ''
    const r = result.result as Record<string, unknown> | undefined
    if (!r || typeof r !== 'object') return ''
    if (typeof r.error === 'string' && r.error) return r.error
    const parts: string[] = []
    if (typeof r.stdout === 'string' && r.stdout) parts.push(r.stdout)
    if (typeof r.stderr === 'string' && r.stderr) parts.push('--- stderr ---\n' + r.stderr)
    return parts.join('\n')
  })()
  return (
    <div className={`tool-line ${done ? (hasError ? 'tool-line--err' : 'tool-line--done') : 'tool-line--run'}`}>
      <div className="tool-line__row">
        {done
          ? <span className="tool-line__icon">{hasError ? '✗' : '✓'}</span>
          : <span className="tool-spinner" />}
        <span className="tool-line__label">
          {done
            ? (hasError ? `${toolDoneLabel(block.name)} — error` : toolDoneLabel(block.name))
            : `${toolRunningLabel(block.name)}…`}
        </span>
        {!done && block.progress && (
          <span className="tool-line__progress">{block.progress}</span>
        )}
        {code && (
          <button className="tool-line__script-toggle" onClick={() => setShowCode(s => !s)}>
            {showCode ? 'Hide script' : 'script'}
          </button>
        )}
        {out && (
          <button className="tool-line__script-toggle" onClick={() => setShowOut(s => !s)}>
            {showOut ? 'Hide output' : 'output'}
          </button>
        )}
      </div>
      {code && showCode && <pre className="tool-line__code"><code>{code}</code></pre>}
      {out && showOut && <pre className="tool-line__code"><code>{out}</code></pre>}
    </div>
  )
}

interface Props {
  message: DisplayMessage
  isStreaming?: boolean
  /** Collapse (hide) tool indicators — used on non-latest messages. */
  collapseTools?: boolean
  /** Attach a highlighted region from a chat figure to the next message. */
  onAnnotate?: (a: Annotation) => void
  /** Global highlight mode (toggled in the chat header) — show a draw surface. */
  highlighting?: boolean
  /** True while any cell has a drag in progress — suppresses the hover surface
   *  on the other cells so the active mark stays locked to its cell. */
  anyDrawing?: boolean
  /** Report a drag starting/ending on this cell (drives anyDrawing). */
  onDrawingChange?: (drawing: boolean) => void
  /** Called when this cell finishes (or aborts) a highlight, to exit the mode. */
  onHighlightDone?: () => void
  /** Increment to erase a drawn mark on chat figures. */
  annotClear?: number
  /** Regenerate this (failed) turn — shown as a retry button on error blocks. */
  onRetry?: () => void
  /** Entities (to resolve chat figures) + pin toggle for the capture gesture. */
  entities?: Entity[]
  onPin?: (id: string, pinned: boolean) => void
  /** Figure IDs currently kept by an active Result — drives the chat pin
   *  state on re-entry (replaces the dropped `entity.pinned` flag). */
  pinnedFigureIds?: Set<string>
  /** Keep (pin) a non-entity message as a snapshot, keyed by content. */
  keptKeys?: Set<string>
  onKeepMessage?: (key: string, text: string, imageUrls: string[], pinned: boolean) => void
  /** Map basename → {url, kind} for files written in this thread (any
   *  tool_result.plots / tables / files entry). Lets inline ` `foo.pdf` ` in
   *  agent prose render as a clickable link to /artifacts/<pid>/<hash><ext>. */
  fileMap?: Map<string, { url: string; kind: 'plot' | 'table' | 'file' }>
  /** A presented plan awaiting a decision (latest message): show Go / Adjust. */
  planActive?: boolean
  onPlanGo?: (saveAsRun: boolean) => void
  onPlanAdjust?: () => void
}

// Stable content hash so a pinned text message can be matched on reload.
function msgKey(s: string): string {
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0
  return 'm' + (h >>> 0).toString(36)
}

export default function Message({ message, isStreaming, collapseTools, onAnnotate, highlighting, anyDrawing, onDrawingChange, onHighlightDone, onRetry, entities, onPin, pinnedFigureIds, keptKeys, onKeepMessage, planActive, onPlanGo, onPlanAdjust, fileMap }: Props) {
  const isUser = message.role === 'user'
  const [showSteps, setShowSteps] = useState(false)
  const visibleBlocks = message.blocks

  // On past messages we collapse the tool/step indicators to keep the thread
  // tidy, but offer an eye toggle to bring them back per message.
  const stepCount = visibleBlocks.filter(b => b.type === 'tool_start').length
  const canCollapse = !!collapseTools && !isStreaming && stepCount > 0
  const hideSteps = canCollapse && !showSteps

  const rendered = renderBlocks(visibleBlocks, hideSteps, onRetry, entities, isUser ? undefined : onPin, planActive, onPlanGo, onPlanAdjust, isStreaming, pinnedFigureIds, fileMap)
  if (rendered.length === 0 && !isStreaming) return null

  const msgText = message.blocks.filter(b => b.type === 'text').map(b => (b as { text: string }).text).join('\n').trim()
  const imageUrls = message.blocks.filter(b => b.type === 'image').map(b => (b as { url: string }).url)

  // Figures are pinned individually in their own headers (a cell can hold
  // several). The toolbar pin handles the no-figure case: keep a text message
  // as a snapshot note (keyed by content).
  const key = msgKey(message.role + '|' + msgText)
  const pinned = !!keptKeys?.has(key)
  const canPin = !isStreaming && imageUrls.length === 0 && msgText.length > 0 && !!onKeepMessage
  const [optimistic, setOptimistic] = useState<boolean | null>(null)
  useEffect(() => { setOptimistic(null) }, [pinned])
  const shownPinned = optimistic ?? pinned
  function togglePin() {
    const next = !shownPinned
    setOptimistic(next)
    onKeepMessage?.(key, msgText, imageUrls, next)
  }

  // Highlight any cell: draw a pink marker over the message, then rasterize
  // the cell (low-res) with the mark composited and attach it + the cell text.
  const contentRef = useRef<HTMLDivElement>(null)
  const strokeRef = useRef<Pt[]>([])
  const [stroke, setStroke] = useState<Pt[]>([])
  const [drawing, setDrawing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [hovered, setHovered] = useState(false)
  // Highlight mode is global (one toggle in the chat header). The draw surface
  // shows only on the cell under the cursor; once a drag starts it locks to
  // that cell (anyDrawing suppresses the hover surface on the others).
  const canHighlight = !!onAnnotate && !isStreaming
  const showSurface = highlighting && canHighlight && ((hovered && !anyDrawing) || drawing)
  useEffect(() => { if (!highlighting) { setStroke([]); strokeRef.current = []; setHovered(false) } }, [highlighting])

  // Normalize (and CLAMP) to this cell's box — so the user can drag past the
  // cell edge without the stroke stopping, while the mark stays inside the
  // cell and only this cell is rasterized.
  function normXY(cx: number, cy: number): Pt {
    const r = contentRef.current!.getBoundingClientRect()
    return {
      x: Math.min(1, Math.max(0, (cx - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (cy - r.top) / r.height)),
    }
  }
  function hlDown(e: React.MouseEvent) {
    const p = normXY(e.clientX, e.clientY)
    strokeRef.current = [p]; setStroke([p]); setDrawing(true); onDrawingChange?.(true)
  }
  // Track the drag at the window level so spilling into other cells keeps the
  // stroke going (clamped); release ends it.
  useEffect(() => {
    if (!drawing) return
    function mv(e: MouseEvent) {
      strokeRef.current = [...strokeRef.current, normXY(e.clientX, e.clientY)]
      setStroke(strokeRef.current)
    }
    function up() { setDrawing(false); onDrawingChange?.(false); if (strokeRef.current.length > 1) attachHighlight() }
    window.addEventListener('mousemove', mv)
    window.addEventListener('mouseup', up)
    return () => { window.removeEventListener('mousemove', mv); window.removeEventListener('mouseup', up) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawing])

  async function attachHighlight() {
    const el = contentRef.current
    const pts = strokeRef.current
    if (!el || pts.length < 2) { onHighlightDone?.(); setStroke([]); strokeRef.current = []; return }
    setBusy(true)
    try {
      const h2c = (await import('html2canvas')).default
      // Render at 2× CSS resolution so figure text (cluster labels, axis
      // tick numbers) is crisp before we downscale. Then cap the long side
      // at 1568px — Anthropic's vision input is downsampled to that anyway,
      // and pre-cap-512 we shipped 512×~380 images where individual
      // cluster digits were ~5px tall and unreadable to the model (it
      // could see the yellow mark but not the number under it).
      const full = await h2c(el, { backgroundColor: '#ffffff', scale: 2, logging: false, useCORS: true })
      const longest = Math.max(full.width, full.height)
      const scale = longest > 1568 ? 1568 / longest : 1
      const W = Math.round(full.width * scale), H = Math.round(full.height * scale)
      const c = document.createElement('canvas'); c.width = W; c.height = H
      const ctx = c.getContext('2d')!
      ctx.drawImage(full, 0, 0, W, H)
      ctx.strokeStyle = HILITE; ctx.lineWidth = Math.max(10, W / 32); ctx.lineCap = 'round'; ctx.lineJoin = 'round'
      ctx.beginPath()
      pts.forEach((p, i) => (i ? ctx.lineTo(p.x * W, p.y * H) : ctx.moveTo(p.x * W, p.y * H)))
      ctx.stroke()
      const b64 = c.toDataURL('image/png').split(',')[1]
      const shape = describeStroke(pts)   // shape + position + size, in concrete prose
      const figCtx = describeHighlightedFigure(el, entities)   // figure entity ref if any
      const onlyImage = imageUrls.length > 0 && (msgText.trim().length === 0)
      const target = onlyImage ? 'figure' : 'message'
      const cellDesc = msgText
        ? `The highlighted message text: "${msgText.slice(0, 500)}".`
        : (figCtx || `The marked element is an image in the chat.`)
      const note =
        `User highlight (this turn): ${shape} on the attached ${target}. ${cellDesc} ` +
        `The mark is a strong topical hint — if the question is short or demonstrative ` +
        `("what is this?", "what are these?", "this", "here"), it's about the marked region. ` +
        `If the question is clearly about the broader figure (axes, comparison to other parts, ` +
        `overall layout), answer that — the mark just points at which figure they mean.`
      onAnnotate?.({ image: b64, note })
    } catch { /* rasterize failed — drop the mark */ }
    finally { setBusy(false); onHighlightDone?.(); setStroke([]); strokeRef.current = [] }
  }

  const strokePts = stroke.map(p => `${p.x * 100},${p.y * 100}`).join(' ')

  return (
    <div className={`msg ${isUser ? 'msg--user' : 'msg--guide'}`}>
      {isUser
        ? <div className="msg__avatar msg__avatar--user">PP</div>
        : <AgentAvatar agent="guide" size={22} />}
      <div className="msg__body"
           onMouseEnter={() => highlighting && setHovered(true)}
           onMouseLeave={() => setHovered(false)}>
        <div className="msg__content" ref={contentRef}>
          {rendered}
          {isStreaming && <span className="cursor-blink">▌</span>}
        </div>
        {showSurface && (
          <div className="msg__hl" onMouseDown={hlDown}>
            {stroke.length > 1 && (
              <svg className="msg__hl-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
                <polyline points={strokePts} fill="none" stroke={HILITE} strokeWidth="16"
                          strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
              </svg>
            )}
            <div className="msg__hl-hint">{busy ? 'capturing…' : 'draw to highlight'}</div>
          </div>
        )}
      </div>

      {/* Per-message toolbar: steps eye + pin. Highlight is a global toggle in
          the chat header now (acts on any cell). */}
      <div className="msg__tools">
        {canCollapse && (
          <button
            className={`msg__tool msg__tool--hover ${showSteps ? 'msg__tool--on' : ''}`}
            onClick={() => setShowSteps(s => !s)}
            title={showSteps ? 'Hide steps' : `Show ${stepCount} step${stepCount > 1 ? 's' : ''} Guide took`}
          >
            {showSteps
              ? <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/><path d="M3 3l18 18" strokeLinecap="round"/></svg>
              : <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>}
            <span className="msg__tool-num">{stepCount}</span>
          </button>
        )}
        {canPin && (
          <button
            className={`msg__tool msg__tool--pin ${shownPinned ? 'msg__tool--pinned' : 'msg__tool--hover'}`}
            onClick={togglePin}
            title={shownPinned ? 'Pinned — click to unpin' : 'Pin to keep this in the project'}
          >
            <svg viewBox="0 0 24 24" fill={shownPinned ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2"><path d="M12 17v5M9 3h6l-1 7 3 3H7l3-3z"/></svg>
          </button>
        )}
      </div>
    </div>
  )
}
