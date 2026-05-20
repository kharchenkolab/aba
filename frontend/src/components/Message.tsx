import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { DisplayMessage, Block } from '../types'
import HighlightableImage from './HighlightableImage'
import { AgentAvatar } from './icons'
import './Message.css'

interface Annotation { image: string; note: string }

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
function renderBlocks(blocks: Block[], collapseTools: boolean, onAnnotate?: (a: Annotation) => void, clearSignal?: number, onRetry?: () => void) {
  const out: React.ReactNode[] = []
  for (let i = 0; i < blocks.length; i++) {
    const b = blocks[i]
    if (b.type === 'text') {
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
      out.push(
        <div key={i} className="msg-image">
          {onAnnotate
            ? <HighlightableImage src={b.url} label={b.alt} onAttach={onAnnotate} hoverToolbar className="msg-image__img" clearSignal={clearSignal} />
            : <img className="msg-image__img" src={b.url} alt={b.alt ?? 'plot'} />}
        </div>,
      )
    } else if (b.type === 'tool_start') {
      if (collapseTools) continue
      // Look ahead for a matching tool_result.
      const result = blocks
        .slice(i + 1)
        .find(x => x.type === 'tool_result') as Extract<Block, { type: 'tool_result' }> | undefined
      const done = !!result
      const hasError = done && 'error' in result!.result
      out.push(
        <div key={i} className={`tool-line ${done ? (hasError ? 'tool-line--err' : 'tool-line--done') : 'tool-line--run'}`}>
          {done
            ? <span className="tool-line__icon">{hasError ? '✗' : '✓'}</span>
            : <span className="tool-spinner" />}
          <span className="tool-line__label">
            {done
              ? (hasError ? `${toolDoneLabel(b.name)} — error` : toolDoneLabel(b.name))
              : `${toolRunningLabel(b.name)}…`}
          </span>
        </div>,
      )
    } else if (b.type === 'tool_result') {
      // Rendered together with its tool_start above; skip.
      continue
    }
  }
  return out
}

interface Props {
  message: DisplayMessage
  isStreaming?: boolean
  /** Hide tool/image blocks (trace panel shows them instead). */
  hideToolBlocks?: boolean
  /** Collapse (hide) tool indicators — used on non-latest messages. */
  collapseTools?: boolean
  /** Attach a highlighted region from a chat figure to the next message. */
  onAnnotate?: (a: Annotation) => void
  /** Increment to erase a drawn mark on chat figures. */
  annotClear?: number
  /** Regenerate this (failed) turn — shown as a retry button on error blocks. */
  onRetry?: () => void
}

const TRACE_TYPES = new Set(['tool_start', 'tool_result', 'image'])

export default function Message({ message, isStreaming, hideToolBlocks, collapseTools, onAnnotate, annotClear, onRetry }: Props) {
  const isUser = message.role === 'user'
  const [showSteps, setShowSteps] = useState(false)
  const visibleBlocks = hideToolBlocks
    ? message.blocks.filter(b => !TRACE_TYPES.has(b.type))
    : message.blocks

  // On past messages we collapse the tool/step indicators to keep the thread
  // tidy, but offer an eye toggle to bring them back per message.
  const stepCount = visibleBlocks.filter(b => b.type === 'tool_start').length
  const canCollapse = !!collapseTools && !isStreaming && !hideToolBlocks && stepCount > 0
  const hideSteps = canCollapse && !showSteps

  const rendered = renderBlocks(visibleBlocks, hideSteps, isUser ? undefined : onAnnotate, annotClear, onRetry)
  if (rendered.length === 0 && !isStreaming) return null

  return (
    <div className={`msg ${isUser ? 'msg--user' : 'msg--guide'}`}>
      {isUser
        ? <div className="msg__avatar msg__avatar--user">PP</div>
        : <AgentAvatar agent="guide" size={22} />}
      <div className="msg__body">
        <div className="msg__content">
          {rendered}
          {isStreaming && <span className="cursor-blink">▌</span>}
        </div>
      </div>
      {canCollapse && (
        <button
          className={`msg__steps-toggle ${showSteps ? 'msg__steps-toggle--on' : ''}`}
          onClick={() => setShowSteps(s => !s)}
          title={showSteps ? 'Hide steps' : `Show ${stepCount} step${stepCount > 1 ? 's' : ''} Guide took`}
        >
          {showSteps ? (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>
              <path d="M3 3l18 18" strokeLinecap="round"/>
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>
            </svg>
          )}
          <span className="msg__steps-count">{stepCount}</span>
        </button>
      )}
    </div>
  )
}
