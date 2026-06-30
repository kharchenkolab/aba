import { useState, useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { prepareAssistantText } from '../markdown/prepareAssistantText'
import type { DisplayMessage, Block, Entity } from '../types'
import { AgentAvatar } from '../components/icons'
import { HILITE, captureHighlight, type Pt } from '../components/highlightTools'
import './Message.css'

interface Annotation { image: string; note: string }
// HILITE, Pt, describeStroke, describeHighlightedFigure, and the default
// chat-cell subcard labeller all live in ./highlightTools now (shared with
// ResultView's per-MemberPanel highlight). This module only renders chat
// messages; the rasterization + crop + note-building logic is in the helper.

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
    Skill: 'read the recipe',
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
// What specific thing did this tool act on? Shown as a dimmer suffix after
// the verb (e.g. "read the recipe — pagoda2-qc", "tools ready — scvi-tools")
// so the chat reads as a narrative of which recipes were loaded and which
// capabilities were prepared, not a generic stream of "Skill / Skill / Skill".
// Scoped tightly: only the two surfaces PK asked for. Easy to extend.
function toolDoneDetail(name: string, input: Record<string, unknown> | undefined): string {
  if (!input || typeof input !== 'object') return ''
  const get = (k: string) => typeof input[k] === 'string' ? (input[k] as string) : ''
  if (name === 'Skill' || name === 'read_skill') {
    return get('skill') || get('name')
  }
  if (name === 'ensure_capability') {
    return get('name') || get('capability')
  }
  return ''
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
    Skill: 'reading the recipe',
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


/** Pin-from-artifact button (Option B / Phase 3): pins an unpinned figure
 *  in chat by materializing the entity via POST /api/artifacts/.../pin.
 *  Surfaces when an `<img>` block carries an artifact_id but no Entity
 *  has materialized yet (the post-cutover default for fresh harvests).
 *  After a successful pin, optimistic UI flips to "pinned" until the
 *  parent refresh delivers the now-materialized Entity (at which point
 *  FigurePin takes over the rendering). */
function ArtifactPin({ artifact_id, onPinned }: {
  artifact_id: string
  onPinned?: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const handle = async () => {
    if (busy || done) return
    setBusy(true)
    try {
      // <exec_id>:<kind>:<idx> — parse defensively in case the producer
      // emitted a malformed id (the backend route is the authoritative
      // validator; we just need URL path components).
      const m = artifact_id.match(/^(.+):([^:]+):(\d+)$/)
      if (!m) { console.warn('ArtifactPin: bad artifact_id', artifact_id); return }
      const [, exec_id, kind, idxs] = m
      const r = await fetch(
        `/api/artifacts/${encodeURIComponent(exec_id)}/${encodeURIComponent(kind)}/${idxs}/pin`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
      )
      if (!r.ok) { console.error('pin_artifact failed', await r.text()); return }
      setDone(true)
      onPinned?.()
    } finally {
      setBusy(false)
    }
  }
  return (
    <button
      className={`msg__tool msg__tool--pin ${done ? 'msg__tool--pinned' : 'msg__tool--hover'}`}
      onClick={handle}
      title={done ? 'Pinned' : busy ? 'Pinning…' : 'Pin this figure'}
      disabled={busy}
    >
      <svg viewBox="0 0 24 24" fill={done ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2"><path d="M12 17v5M9 3h6l-1 7 3 3H7l3-3z"/></svg>
    </button>
  )
}

// Auto-fire countdown for the active plan card. The Go button has a 60s
// default: if the biologist doesn't intervene, the plan runs on its own. A
// clock-style ring beside Go shows the time draining away.
const PLAN_AUTOFIRE_MS = 60_000
const PLAN_TICK_MS = 250

function PlanCard({ block, active, onGo, onAdjust }: {
  block: Extract<Block, { type: 'plan' }>
  active: boolean; onGo?: (saveAsRun: boolean) => void; onAdjust?: () => void
}) {
  // Pre-checked: by default a plan's outputs group into one Run (see open_run).
  // Unchecking rides along on the Go message as a hint to skip it.
  const [saveAsRun, setSaveAsRun] = useState(true)
  // Remaining time on the auto-fire timer (ms). Drives the ring + auto-fire.
  const [remainingMs, setRemainingMs] = useState(PLAN_AUTOFIRE_MS)
  // Refs let the interval read the *latest* saveAsRun / onGo without
  // re-arming the timer on every checkbox toggle or parent re-render.
  const saveAsRunRef = useRef(saveAsRun); saveAsRunRef.current = saveAsRun
  const onGoRef = useRef(onGo); onGoRef.current = onGo
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // Guard against double-firing (manual click racing the final tick) and
  // against firing after the card unmounts.
  const firedRef = useRef(false)

  const stopTimer = () => {
    if (intervalRef.current != null) { clearInterval(intervalRef.current); intervalRef.current = null }
  }
  // Fire the plan exactly once, honoring the current "Save as a run" choice.
  const fire = () => {
    if (firedRef.current) return
    firedRef.current = true
    stopTimer()
    onGoRef.current?.(saveAsRunRef.current)
  }
  // Adjust (or anything that drops out of the active state) cancels the timer.
  const adjust = () => { stopTimer(); onAdjust?.() }

  // Arm the countdown whenever this becomes the active plan with a Go handler.
  // Keyed on `hasGo` (a stable boolean) rather than `onGo`'s identity so an
  // inline parent callback doesn't restart the timer on every render.
  const hasGo = !!onGo
  useEffect(() => {
    if (!active || !hasGo) return
    firedRef.current = false
    setRemainingMs(PLAN_AUTOFIRE_MS)
    const startedAt = Date.now()
    intervalRef.current = setInterval(() => {
      const left = Math.max(0, PLAN_AUTOFIRE_MS - (Date.now() - startedAt))
      setRemainingMs(left)
      if (left <= 0) fire()
    }, PLAN_TICK_MS)
    return stopTimer
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, hasGo])
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
      {active && (() => {
        // Timer state for the Go button's auto-fire indicator. `frac` is the
        // share of time STILL REMAINING — the solid pie wedge below "Go"
        // sweeps that arc and shrinks to nothing at zero, then fires.
        const frac = hasGo
          ? Math.max(0, Math.min(1, remainingMs / PLAN_AUTOFIRE_MS))
          : 0
        const secs = Math.ceil(remainingMs / 1000)
        const goTitle = hasGo
          ? `Auto-runs in ${secs}s — click to run now, or Adjust… to cancel`
          : 'Run this plan'
        return (
          <div className="plan-card__actions">
            <button className="plan-card__go" onClick={fire} title={goTitle}
                    aria-label={hasGo ? `Run plan (auto-runs in ${secs} seconds)` : 'Run plan'}>
              <span className="plan-card__go-label">Go</span>
              {hasGo && (
                <span className="plan-card__go-timer" aria-hidden="true"
                      style={{ ['--frac' as string]: `${frac * 360}deg` }} />
              )}
            </button>
            <button className="plan-card__adjust" onClick={adjust}
                    title={hasGo ? 'Cancel the auto-run and revise the plan'
                                 : 'Revise the plan'}>Adjust…</button>
            <label className="plan-card__saverun"
                   title="Group this plan's outputs into one Run in the project tree">
              <input type="checkbox" checked={saveAsRun}
                     onChange={e => setSaveAsRun(e.target.checked)} />
              Save as a run
            </label>
          </div>
        )
      })()}
    </div>
  )
}

function renderBlocks(blocks: Block[], collapseTools: boolean, onRetry?: () => void, entities?: Entity[], onPin?: (id: string, pinned: boolean) => void, isUser?: boolean,
                      planActive?: boolean, onPlanGo?: (saveAsRun: boolean) => void, onPlanAdjust?: () => void,
                      isStreaming?: boolean, pinnedFigureIds?: Set<string>,
                      fileMap?: Map<string, { url: string; kind: 'plot' | 'table' | 'file' }>,
                      currentRunId?: string | null,
                      onArtifactPinned?: () => void) {
  const out: React.ReactNode[] = []
  // Browsers refuse `file://` URLs from a web page, so any `file:///path` the
  // agent emits would render as a broken `<img>` or a dead `<a>`. Rewrite to
  // the matching same-origin server path (`file:///artifacts/...` → `/artifacts/...`,
  // `file:///path/that/doesn't/start/with/a/known/route` → leave it; the
  // backend's /artifacts route serves the project's artifact dir). The
  // markdown image is still duplicative with the UI-rendered figure from
  // the tool_result, but the prompt steers the agent away from that.
  const fixFileUrl = (u: string | undefined): string => {
    if (!u) return ''
    return u.startsWith('file://') ? u.replace(/^file:\/\//, '') : u
  }
  // Markdown overrides. Always present so the `img`/`a` rewrites apply even
  // when fileMap is empty.
  const mdComponents = {
    img: (props: { src?: string; alt?: string }) => (
      <img src={fixFileUrl(props.src)} alt={props.alt ?? ''} />
    ),
    a: (props: { href?: string; children?: React.ReactNode; title?: string }) => {
      const href = fixFileUrl(props.href)
      const isArtifact = (props.href || '').startsWith('/artifacts/')
      // For artifact links, derive a human-readable download filename
      // from the visible link text. Without this the browser falls back
      // to the URL's basename, which is the content-hash for /artifacts/
      // paths ("183906e6...pdf") and reads as a broken/garbage filename
      // when the user hits Save. The visible text is the agent's own
      // label (e.g. "Open umap_leiden.pdf") — strip a leading "Open "
      // verb so the saved file is just `umap_leiden.pdf`.
      let downloadName: string | undefined
      if (isArtifact) {
        const txt = (typeof props.children === 'string' ? props.children : '').trim()
        const m = /([\w.\-+]+\.(?:pdf|svg|csv|tsv|html?|rds|h5ad|h5|parquet|xlsx|json|md|txt|png|jpe?g|webp|gif))$/i.exec(txt)
        if (m) downloadName = m[1]
      }
      // Feedback links (mailto:) get the bug glyph — the same icon as the
      // header's Report-a-bug button, so the marker is consistent everywhere.
      if ((href || '').startsWith('mailto:')) {
        return (
          <a className="feedback-link" href={href} title={props.title} rel="noreferrer">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="m8 2 1.88 1.88M14.12 3.88 16 2"/><path d="M9 7.13v-1a3 3 0 1 1 6 0v1"/>
              <path d="M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6zM12 20v-9"/>
              <path d="M6.53 9C4.6 8.8 3 7.1 3 5M6 13H2M3 21c0-2.1 1.7-3.9 3.8-4M20.97 5c0 2.1-1.6 3.8-3.5 4M22 13h-4M17.2 17c2.1.1 3.8 1.9 3.8 4"/>
            </svg>
            {props.children}
          </a>
        )
      }
      return (
        <a href={href} title={props.title}
           target={isArtifact ? '_blank' : undefined}
           download={downloadName}
           rel="noreferrer">{props.children}</a>
      )
    },
    // Override inline `code` so basenames the agent quotes resolve to a link
    // (only when the basename actually corresponds to a file written this
    // thread). Bare code (variable names, identifiers) renders unchanged.
    ...(fileMap && fileMap.size > 0 ? {
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
    } : {}),
  }
  for (let i = 0; i < blocks.length; i++) {
    const b = blocks[i]
    if (b.type === 'plan') {
      out.push(<PlanCard key={i} block={b} active={!!planActive} onGo={onPlanGo} onAdjust={onPlanAdjust} />)
    } else if (b.type === 'text') {
      // User-typed text renders as PLAIN text (pre-wrap) — pasted ascii tables,
      // pipes, dashes, etc. shouldn't trigger markdown/gfm parsing into a
      // half-baked rich-text render (PK 2026-06-02). What the user typed is
      // what they see. The agent's text still goes through ReactMarkdown
      // because it does intentionally author markdown (lists, code blocks,
      // bold, the file-link patterns the mdComponents above rewrite).
      if (isUser) {
        out.push(
          <div key={i} className="msg-text msg-text--plain"
               style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {b.text}
          </div>,
        )
      } else {
        // prepareAssistantText converts <reasoning>/<thinking> blocks (model
        // scratchpad fallback when given conflicting context) into Markdown
        // blockquotes so they render visibly instead of tripping the renderer.
        const md = prepareAssistantText(b.text)
        out.push(
          <div key={i} className="msg-text">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>{md}</ReactMarkdown>
          </div>,
        )
      }
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
    } else if (b.type === 'attachments') {
      // User-attached files (paperclip / paste), persisted on the user
      // message. A row of chips; images render an inline thumbnail that
      // opens to the lightbox (reusing ZoomableImg), non-images a file chip.
      if (b.items.length === 0) continue
      out.push(
        <div key={i} className="msg-attachments">
          {b.items.map((it, j) => (
            it.is_image ? (
              <div key={j} className="msg-attachment msg-attachment--image" title={it.name}>
                <ZoomableImg src={it.url} alt={it.name} />
                <span className="msg-attachment__name">{it.name}</span>
              </div>
            ) : (
              <a key={j} className="msg-attachment msg-attachment--file"
                 href={it.url} target="_blank" rel="noreferrer" title={it.name}>
                <svg className="msg-attachment__icon" width="15" height="15" viewBox="0 0 24 24"
                     fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round">
                  <path d="M6 3h9l5 5v13H6z" /><path d="M14 3v6h6" />
                </svg>
                <span className="msg-attachment__name">{it.name}</span>
                <span className="msg-attachment__kind">{it.kind}</span>
              </a>
            )
          ))}
        </div>,
      )
    } else if (b.type === 'image') {
      // Title from the registered figure/table entity, if any. (Highlight + pin
      // now live in the per-message toolbar and act on the whole cell.)
      const ent = entities?.find(e => e.artifact_path === b.url && (e.type === 'figure' || e.type === 'table'))
      // Option B / Phase 3: when no entity exists yet (the post-cutover
      // default for fresh harvests), pin via artifact_id materializes
      // the entity on-click. Otherwise the legacy FigurePin path handles
      // the toggle.
      const artifactId = (b as { artifact_id?: string }).artifact_id
      // Display source: preview rasterization (for PDF/non-raster
      // canonicals) takes precedence over the canonical so <img> renders
      // something the browser actually displays. Canonical stays the
      // download target for any future "open original" affordance.
      const previewUrl = (b as { preview_url?: string }).preview_url
      const displaySrc = previewUrl ?? b.url
      // Build the pin control once, reuse it in BOTH the inline frame
      // (top-right corner) AND the lightbox overlay — the chat's
      // zoom view used to be pinless, surprising users who expected
      // the gesture to follow the figure.
      const pinSlot = ent && onPin
        ? <FigurePin entity={ent} isPinned={!!pinnedFigureIds?.has(ent.id)} onPin={onPin} />
        : artifactId
          ? <ArtifactPin artifact_id={artifactId} onPinned={onArtifactPinned} />
          : null
      out.push(
        <div key={i} className="msg-image">
          {ent && <div className="msg-image__head"><span className="msg-image__title">{ent.title}</span></div>}
          <div className="msg-image__frame">
            <ZoomableImg src={displaySrc} alt={b.alt ?? 'plot'} pinSlot={pinSlot} />
            {pinSlot && <div className="msg-image__tools">{pinSlot}</div>}
          </div>
        </div>,
      )
    } else if (b.type === 'tool_start') {
      if (collapseTools) continue
      // Look ahead for a matching tool_result.
      const result = blocks
        .slice(i + 1)
        .find(x => x.type === 'tool_result') as Extract<Block, { type: 'tool_result' }> | undefined
      out.push(<ToolLine key={i} block={b} result={result} currentRunId={currentRunId} />)
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
function ZoomableImg({ src, alt, pinSlot }: {
  src: string
  alt: string
  /** Optional pin control rendered in the lightbox toolbar so the
   *  gesture follows the figure into zoom — same component the inline
   *  frame uses, so pinned-state stays consistent. */
  pinSlot?: ReactNode
}) {
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
          {/* Frame shrinks to the image's rendered size; toolbar sits
              above its left edge — same idea as the Run-view modal's
              header bar but without the box chrome. */}
          <div className="lightbox__frame" onClick={e => e.stopPropagation()}>
            <div className="lightbox__tools">
              {pinSlot}
              <button className="lightbox__close" onClick={() => setOpen(false)} aria-label="Close">×</button>
            </div>
            <img className="lightbox__img" src={src} alt={alt}
              crossOrigin={cors ? 'anonymous' : undefined} />
          </div>
        </div>
      )}
    </>
  )
}

// to carry, now available per cell.
function ToolLine({ block, result, currentRunId }: {
  block: Extract<Block, { type: 'tool_start' }>
  result?: Extract<Block, { type: 'tool_result' }>
  currentRunId?: string | null
}) {
  const [showCode, setShowCode] = useState(false)
  const [showOut, setShowOut] = useState(false)
  const done = !!result
  // Fix #5 — tool returned {deferred:true,job_id}. The turn is halted in
  // AWAITING_TOOL_RESULT; the eventual tool_result is delivered by the
  // job-complete webhook. Until that lands, render a queued badge with the
  // job id instead of the running spinner so the chat is unblocked-looking.
  const deferred = !done && !!(block as { deferred?: boolean }).deferred
  const deferredJobId = (block as { deferredJobId?: string }).deferredJobId
  // Failure detection from structured fields — no need to interpret output
  // text. Covers all three failure shapes (PK 2026-06-03):
  //   - top-level `error` string: tool-wrapper failed (timeout, kernel crash)
  //   - status === 'error': nextflow runner's failure shape
  //   - returncode !== 0: the cell itself raised
  // status === 'cancelled' is a user action (Stop), explicitly NOT an error.
  const hasError = (() => {
    if (!done) return false
    const r = result!.result as Record<string, unknown>
    if (typeof r.error === 'string' && r.error) return true
    if (r.status === 'cancelled') return false
    if (r.status === 'error') return true
    if (typeof r.returncode === 'number' && r.returncode !== 0) return true
    return false
  })()
  const code = typeof block.input?.code === 'string' ? (block.input.code as string) : ''

  // #334 live-stream view from tool_chunk events accumulated on the block.
  // While !done, prefer the live buffer; on completion, switch to the
  // finalized result.stdout/stderr (the snipped 50K version the model saw).
  const liveStdout = (block as { liveStdout?: string }).liveStdout || ''
  const liveStderr = (block as { liveStderr?: string }).liveStderr || ''
  const liveBytesStdout = (block as { liveBytesStdout?: number }).liveBytesStdout || 0
  const liveBytesStderr = (block as { liveBytesStderr?: number }).liveBytesStderr || 0
  const liveElapsedS = (block as { liveElapsedS?: number }).liveElapsedS || 0
  const lastChunkAt = (block as { lastChunkAt?: number }).lastChunkAt
  const liveHas = !!(liveStdout || liveStderr)

  // Re-render the "Xs ago" / elapsed counter at ~1Hz while live so the header
  // doesn't sit frozen between coalescer flushes.
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (done || !liveHas) return
    const h = window.setInterval(() => setTick(n => n + 1), 1000)
    return () => window.clearInterval(h)
  }, [done, liveHas])
  void tick

  // Build a textual rendering of the tool's stdout/stderr (post-2026-05-31:
  // chat-side counterpart to "script" — peek at what the cell actually printed).
  // Structured fields (plots, tables) render elsewhere in the chat already;
  // here we surface the raw text streams + the error blob when there is one.
  const finalOut: string = (() => {
    if (!result) return ''
    const r = result.result as Record<string, unknown> | undefined
    if (!r || typeof r !== 'object') return ''
    if (typeof r.error === 'string' && r.error) return r.error
    const parts: string[] = []
    if (typeof r.stdout === 'string' && r.stdout) parts.push(r.stdout)
    if (typeof r.stderr === 'string' && r.stderr) parts.push('--- stderr ---\n' + r.stderr)
    // Structured-tool errors (e.g. ensure_capability) carry `note` + `diagnostic`
    // instead of stdout/stderr — surface them so a failed chip has something to
    // expand (otherwise the user sees "✗ error" with no way to read what broke).
    if (!parts.length && hasError) {
      if (typeof r.note === 'string' && r.note) parts.push(r.note)
      if (typeof r.diagnostic === 'string' && r.diagnostic)
        parts.push('--- diagnostic ---\n' + r.diagnostic)
    }
    return parts.join('\n')
  })()
  const liveOut: string = (() => {
    if (!liveHas) return ''
    const parts: string[] = []
    if (liveStdout) parts.push(liveStdout)
    if (liveStderr) parts.push('--- stderr ---\n' + liveStderr)
    return parts.join('\n')
  })()
  const out = done ? finalOut : liveOut
  const showOutToggleable = !!out
  // Output pane stays HIDDEN by default — opening it on every tool call
  // makes long runs feel like a popup storm (PK 2026-06-03). Liveness is
  // surfaced via a small pulsing dot + byte counter on the "output" button
  // itself, so the user sees something is happening without the pane
  // taking over the chat.

  // #334 Phase 2 — rehydrate orphan tool_starts from the server buffer on
  // mount. Runs once. Fires only when: we have a tool_use_id + currentRunId,
  // no final result yet, no live text yet (SSE replay would have populated
  // it). 404 = buffer GC'd → silent no-op. Subsequent SSE chunks are
  // dedupe-gated by bytes_total in useChat so a replay landing AFTER this
  // rehydrate won't duplicate output.
  const blockUseId = (block as { tool_use_id?: string }).tool_use_id
  const rehydratedRef = useRef(false)
  useEffect(() => {
    if (rehydratedRef.current) return
    if (!blockUseId || !currentRunId) return
    if (done || liveHas) return
    rehydratedRef.current = true
    const url = `/api/turns/${encodeURIComponent(currentRunId)}/tool_stream/${encodeURIComponent(blockUseId)}`
    fetch(url).then(r => r.ok ? r.json() : null).then(snap => {
      if (!snap) return
      const b = block as {
        liveStdout?: string; liveStderr?: string;
        liveBytesStdout?: number; liveBytesStderr?: number;
        liveElapsedS?: number; lastChunkAt?: number;
      }
      if (typeof snap.stdout === 'string' && snap.stdout) b.liveStdout = snap.stdout
      if (typeof snap.stderr === 'string' && snap.stderr) b.liveStderr = snap.stderr
      b.liveBytesStdout = snap.bytes_stdout || 0
      b.liveBytesStderr = snap.bytes_stderr || 0
      b.liveElapsedS = snap.elapsed_s || 0
      b.lastChunkAt = Date.now()
      setTick(n => n + 1)
    }).catch(() => { /* silent — drawer keeps whatever it had */ })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const totalLiveBytes = liveBytesStdout + liveBytesStderr
  const elapsedFmt = (s: number) => {
    if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`
    const m = Math.floor(s / 60), r = Math.floor(s % 60)
    return `${m}:${String(r).padStart(2, '0')}`
  }
  const bytesFmt = (b: number) => b < 1024 ? `${b} B` : `${(b / 1024).toFixed(1)} KB`
  const sinceLastChunk = lastChunkAt ? Math.floor((Date.now() - lastChunkAt) / 1000) : 0

  return (
    <div className={`tool-line ${done ? (hasError ? 'tool-line--err' : 'tool-line--done') : (deferred ? 'tool-line--queued' : 'tool-line--run')}`}>
      <div className="tool-line__row">
        {done
          ? <span className="tool-line__icon">{hasError ? '✗' : '✓'}</span>
          : deferred
            ? <span className="tool-line__icon" title="queued — running in background">⏳</span>
            : <span className="tool-spinner" />}
        <span className="tool-line__label">
          {done
            ? (hasError ? `${toolDoneLabel(block.name)} — error` : toolDoneLabel(block.name))
            : deferred
              ? `${toolDoneLabel(block.name)} — queued${deferredJobId ? ` (${deferredJobId})` : ''}`
              : `${toolRunningLabel(block.name)}…`}
          {(() => {
            const detail = toolDoneDetail(block.name, block.input as Record<string, unknown> | undefined)
            return detail ? <span className="tool-line__detail"> — {detail}</span> : null
          })()}
        </span>
        {!done && block.progress && (
          <span className="tool-line__progress">{block.progress}</span>
        )}
        {code && (
          <button className="tool-line__script-toggle" onClick={() => setShowCode(s => !s)}>
            {showCode ? 'Hide script' : 'script'}
          </button>
        )}
        {showOutToggleable && (() => {
          // Three states:
          //   - running with live activity: pulsing green dot + byte count
          //   - done with error: fixed red dot + red border (no pulse — pulse
          //     means "live"; fixed means "status")
          //   - else: plain "output"
          const liveActive = !done && liveHas
          const cls = liveActive
            ? 'tool-line__script-toggle tool-line__script-toggle--live'
            : (done && hasError)
              ? 'tool-line__script-toggle tool-line__script-toggle--err'
              : 'tool-line__script-toggle'
          const title = liveActive
            ? `live: ${bytesFmt(totalLiveBytes)} streamed · click to view`
            : (done && hasError) ? 'tool returned an error — click to view'
            : undefined
          return (
            <button className={cls} onClick={() => setShowOut(s => !s)} title={title}>
              {liveActive && <span className="tool-line__live-dot" />}
              {done && hasError && <span className="tool-line__err-dot" />}
              {showOut ? 'Hide output' : 'output'}
              {liveActive && (
                <span className="tool-line__output-meta">
                  {' '}{bytesFmt(totalLiveBytes)}
                </span>
              )}
            </button>
          )
        })()}
      </div>
      {code && showCode && <pre className="tool-line__code"><code>{code}</code></pre>}
      {showOutToggleable && showOut && (
        <div className="tool-line__output">
          {!done && (
            <div className="tool-line__live-header" aria-live="polite">
              <span className="tool-line__live-badge"><span className="tool-line__live-dot" />LIVE</span>
              <span className="tool-line__live-meta">
                {elapsedFmt(liveElapsedS)} · {bytesFmt(totalLiveBytes)}
                {sinceLastChunk > 3 ? ` · idle ${sinceLastChunk}s` : ''}
              </span>
            </div>
          )}
          {done && liveHas && (
            <div className="tool-line__live-header tool-line__live-header--done">
              <span className="tool-line__live-badge tool-line__live-badge--done">✓ DONE</span>
              <span className="tool-line__live-meta">{bytesFmt(totalLiveBytes)} streamed</span>
            </div>
          )}
          <pre className="tool-line__code"><code>{out}</code></pre>
        </div>
      )}
    </div>
  )
}

interface Props {
  message: DisplayMessage
  isStreaming?: boolean
  /** #334 Phase 2 — current Turn's run_id, threaded to ToolStep so an
   *  orphan tool_start can rehydrate via /api/turns/{runId}/tool_stream/{tu}. */
  currentRunId?: string | null
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
  /** Called after a successful artifact pin (Option B / Phase 3) so the
   *  parent can refresh the entity list and reveal the right rail. The
   *  artifact pin doesn't go through `onPin` because that would
   *  double-create a Result (artifact pin already wraps the new entity).
   *  Distinct callback keeps the side effects exactly: refresh + reveal. */
  onArtifactPinned?: () => void
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

export default function Message({ message, isStreaming, collapseTools, onAnnotate, highlighting, anyDrawing, onDrawingChange, onHighlightDone, onRetry, entities, onPin, onArtifactPinned, pinnedFigureIds, keptKeys, onKeepMessage, planActive, onPlanGo, onPlanAdjust, fileMap, currentRunId }: Props) {
  const isUser = message.role === 'user'
  const [showSteps, setShowSteps] = useState(false)
  const visibleBlocks = message.blocks

  // On past messages we collapse the tool/step indicators to keep the thread
  // tidy, but offer an eye toggle to bring them back per message.
  const stepCount = visibleBlocks.filter(b => b.type === 'tool_start').length
  const hasError = visibleBlocks.some(b => b.type === 'error')
  const canCollapse = !!collapseTools && !isStreaming && stepCount > 0
  // Errored cells default to expanded so the intermediate tool calls /
  // outputs that led to the failure stay visible alongside the red error
  // line. The eye toggle still works for manual collapse.
  const hideSteps = canCollapse && !showSteps && !hasError

  const rendered = renderBlocks(visibleBlocks, hideSteps, onRetry, entities, isUser ? undefined : onPin, isUser, planActive, onPlanGo, onPlanAdjust, isStreaming, pinnedFigureIds, fileMap, currentRunId, isUser ? undefined : onArtifactPinned)
  if (rendered.length === 0 && !isStreaming) return null

  const msgText = message.blocks.filter(b => b.type === 'text').map(b => (b as { text: string }).text).join('\n').trim()
  // Phase C — auto-continuation marker. A user-role message that starts
  // with "[continuation:" was synthesized by the runner when a background
  // job finished; render it with a distinct cog avatar + soft styling so
  // it's obvious the user didn't actually type this. Phase B will move the
  // marker into a proper messages-row metadata column; pattern-matching is
  // the MVP.
  const isContinuation = isUser && msgText.startsWith('[continuation:')
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

  // Copy the message's text content to the clipboard. Briefly flips to a
  // check mark so the click registers visually. Image-only cells (no text)
  // hide the button — there's nothing to copy.
  const [copied, setCopied] = useState(false)
  const canCopy = msgText.length > 0 && !isStreaming
  async function copyMessage() {
    try {
      await navigator.clipboard.writeText(msgText)
      setCopied(true)
      setTimeout(() => setCopied(false), 1100)
    } catch (err) {
      console.error('clipboard write failed', err)
    }
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
      const onlyImage = imageUrls.length > 0 && (msgText.trim().length === 0)
      const result = await captureHighlight({
        cellEl: el,
        ptsNorm: pts,
        subcardSelector: '.msg-text, .msg-image, .msg-notice, .msg-error, .plan-card, .tool-line',
        entities,
        cellText: msgText,
        onlyImage,
      })
      if (result) onAnnotate?.(result)
    } catch { /* rasterize failed — drop the mark */ }
    finally { setBusy(false); onHighlightDone?.(); setStroke([]); strokeRef.current = [] }
  }

  const strokePts = stroke.map(p => `${p.x * 100},${p.y * 100}`).join(' ')

  return (
    <div className={`msg ${isUser ? 'msg--user' : 'msg--guide'} ${isContinuation ? 'msg--continuation' : ''} ${isStreaming ? 'msg--streaming' : ''}`}>
      {isContinuation
        ? <div className="msg__avatar msg__avatar--cont" title="Auto-continuation from a background job">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M21 12a9 9 0 1 1-3-6.7" /><path d="M21 4v5h-5" />
            </svg>
          </div>
        : isUser
          ? <div className="msg__avatar msg__avatar--user">PP</div>
          : <AgentAvatar agent="guide" size={22} />}
      <div className="msg__body"
           onMouseEnter={() => highlighting && setHovered(true)}
           onMouseLeave={() => setHovered(false)}>
        <div className="msg__content" ref={contentRef}>
          {rendered}
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
      {canCopy && (
        <div className="msg__tools msg__tools--bottom">
          <button
            className={`msg__tool msg__tool--hover ${copied ? 'msg__tool--on' : ''}`}
            onClick={copyMessage}
            title={copied ? 'Copied!' : 'Copy message text'}
          >
            {copied
              ? <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><polyline points="4 12 10 18 20 6"/></svg>
              : <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></svg>}
          </button>
        </div>
      )}
    </div>
  )
}
