import { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { DisplayMessage, Block, Entity } from '../types'
import { AgentAvatar } from './icons'
import './Message.css'

interface Annotation { image: string; note: string }
const HILITE = 'rgba(253, 224, 71, 0.55)'   // highlighter yellow
type Pt = { x: number; y: number }

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

function toolDoneLabel(name: string) {
  const labels: Record<string, string> = {
    list_data_files: 'Listed data files',
    read_csv_info: 'Read CSV',
    run_python: 'Ran Python',
    get_provenance: 'Traced provenance',
    get_dependents: 'Found dependents',
    create_scenario: 'Created scenario',
  }
  return labels[name] ?? name
}
function toolRunningLabel(name: string) {
  const labels: Record<string, string> = {
    list_data_files: 'listing data files',
    read_csv_info: 'reading CSV',
    run_python: 'running Python',
    get_provenance: 'tracing provenance',
    get_dependents: 'finding dependents',
    create_scenario: 'creating scenario',
  }
  return labels[name] ?? name
}

/**
 * Render a message's blocks, merging each tool_start with its following
 * tool_result into a single indicator that resolves spinner → green check.
 * Tool indicators are hidden when `collapseTools` is set (older messages).
 */
/** Per-figure pin (a cell can hold several plots — each is its own entity).
 *  Icon-only, hover-reveal in the plot's top-right corner — same style as the
 *  message toolbar buttons. */
function FigurePin({ entity, onPin }: { entity: Entity; onPin: (id: string, pinned: boolean) => void }) {
  const [opt, setOpt] = useState<boolean | null>(null)
  useEffect(() => { setOpt(null) }, [entity.pinned])
  const pinned = opt ?? entity.pinned
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
  active: boolean; onGo?: () => void; onAdjust?: () => void
}) {
  return (
    <div className="plan-card">
      <div className="plan-card__head">
        <span className="plan-card__spark">✦</span>{block.title || 'Plan'}
      </div>
      {block.rationale && <div className="plan-card__why">{block.rationale}</div>}
      <ol className="plan-card__steps">
        {(Array.isArray(block.steps) ? block.steps : []).map((s, i) => <li key={i}>{String(s)}</li>)}
      </ol>
      {active && (
        <div className="plan-card__actions">
          <button className="plan-card__go" onClick={onGo}>Go</button>
          <button className="plan-card__adjust" onClick={onAdjust}>Adjust…</button>
        </div>
      )}
    </div>
  )
}

function renderBlocks(blocks: Block[], collapseTools: boolean, onRetry?: () => void, entities?: Entity[], onPin?: (id: string, pinned: boolean) => void,
                      planActive?: boolean, onPlanGo?: () => void, onPlanAdjust?: () => void) {
  const out: React.ReactNode[] = []
  for (let i = 0; i < blocks.length; i++) {
    const b = blocks[i]
    if (b.type === 'plan') {
      out.push(<PlanCard key={i} block={b} active={!!planActive} onGo={onPlanGo} onAdjust={onPlanAdjust} />)
    } else if (b.type === 'text') {
      out.push(
        <div key={i} className="msg-text">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{b.text}</ReactMarkdown>
        </div>,
      )
    } else if (b.type === 'error') {
      out.push(<ErrorLine key={i} text={b.text} detail={b.detail} onRetry={onRetry} />)
    } else if (b.type === 'notice') {
      out.push(
        <div key={i} className="msg-notice">
          <span className="tool-spinner" />
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
            <img className="msg-image__img" src={b.url} alt={b.alt ?? 'plot'} crossOrigin="anonymous" />
            {ent && onPin && (
              <div className="msg-image__tools">
                <FigurePin entity={ent} onPin={onPin} />
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
// to carry, now available per cell.
function ToolLine({ block, result }: {
  block: Extract<Block, { type: 'tool_start' }>
  result?: Extract<Block, { type: 'tool_result' }>
}) {
  const [showCode, setShowCode] = useState(false)
  const done = !!result
  const hasError = done && 'error' in result!.result
  const code = typeof block.input?.code === 'string' ? (block.input.code as string) : ''
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
        {code && (
          <button className="tool-line__script-toggle" onClick={() => setShowCode(s => !s)}>
            {showCode ? 'Hide script' : 'Show script'}
          </button>
        )}
      </div>
      {code && showCode && <pre className="tool-line__code"><code>{code}</code></pre>}
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
  /** Keep (pin) a non-entity message as a snapshot, keyed by content. */
  keptKeys?: Set<string>
  onKeepMessage?: (key: string, text: string, imageUrls: string[], pinned: boolean) => void
  /** A presented plan awaiting a decision (latest message): show Go / Adjust. */
  planActive?: boolean
  onPlanGo?: () => void
  onPlanAdjust?: () => void
}

// Stable content hash so a pinned text message can be matched on reload.
function msgKey(s: string): string {
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0
  return 'm' + (h >>> 0).toString(36)
}

export default function Message({ message, isStreaming, collapseTools, onAnnotate, highlighting, anyDrawing, onDrawingChange, onHighlightDone, onRetry, entities, onPin, keptKeys, onKeepMessage, planActive, onPlanGo, onPlanAdjust }: Props) {
  const isUser = message.role === 'user'
  const [showSteps, setShowSteps] = useState(false)
  const visibleBlocks = message.blocks

  // On past messages we collapse the tool/step indicators to keep the thread
  // tidy, but offer an eye toggle to bring them back per message.
  const stepCount = visibleBlocks.filter(b => b.type === 'tool_start').length
  const canCollapse = !!collapseTools && !isStreaming && stepCount > 0
  const hideSteps = canCollapse && !showSteps

  const rendered = renderBlocks(visibleBlocks, hideSteps, onRetry, entities, isUser ? undefined : onPin, planActive, onPlanGo, onPlanAdjust)
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
      const full = await h2c(el, { backgroundColor: '#ffffff', scale: 1, logging: false, useCORS: true })
      const scale = full.width > 512 ? 512 / full.width : 1
      const W = Math.round(full.width * scale), H = Math.round(full.height * scale)
      const c = document.createElement('canvas'); c.width = W; c.height = H
      const ctx = c.getContext('2d')!
      ctx.drawImage(full, 0, 0, W, H)
      ctx.strokeStyle = HILITE; ctx.lineWidth = Math.max(10, W / 32); ctx.lineCap = 'round'; ctx.lineJoin = 'round'
      ctx.beginPath()
      pts.forEach((p, i) => (i ? ctx.lineTo(p.x * W, p.y * H) : ctx.moveTo(p.x * W, p.y * H)))
      ctx.stroke()
      const b64 = c.toDataURL('image/png').split(',')[1]
      const desc = msgText ? ` The text of the highlighted message: "${msgText.slice(0, 500)}".` : ''
      onAnnotate?.({
        image: b64,
        note: `The user highlighted a region of a message in the conversation — marked in translucent yellow on the attached image.${desc} Answer about the highlighted region specifically (they may refer to it as "here").`,
      })
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
