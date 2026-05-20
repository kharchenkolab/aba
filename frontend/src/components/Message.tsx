import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { DisplayMessage, Block } from '../types'
import './Message.css'

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
function renderBlocks(blocks: Block[], collapseTools: boolean) {
  const out: React.ReactNode[] = []
  for (let i = 0; i < blocks.length; i++) {
    const b = blocks[i]
    if (b.type === 'text') {
      out.push(
        <div key={i} className="msg-text">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{b.text}</ReactMarkdown>
        </div>,
      )
    } else if (b.type === 'image') {
      out.push(
        <div key={i} className="msg-image">
          <img src={b.url} alt={b.alt ?? 'plot'} />
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
}

const TRACE_TYPES = new Set(['tool_start', 'tool_result', 'image'])

export default function Message({ message, isStreaming, hideToolBlocks, collapseTools }: Props) {
  const isUser = message.role === 'user'
  const visibleBlocks = hideToolBlocks
    ? message.blocks.filter(b => !TRACE_TYPES.has(b.type))
    : message.blocks

  const rendered = renderBlocks(visibleBlocks, !!collapseTools && !isStreaming)
  if (rendered.length === 0 && !isStreaming) return null

  return (
    <div className={`msg ${isUser ? 'msg--user' : 'msg--guide'}`}>
      <div className={`msg__avatar ${isUser ? 'msg__avatar--user' : 'msg__avatar--guide'}`}>
        {isUser ? 'PP' : (
          <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor">
            <path d="M10 2a8 8 0 100 16A8 8 0 0010 2zm0 3a1.5 1.5 0 110 3 1.5 1.5 0 010-3zm0 10c-2.2 0-4.1-1.1-5.3-2.8.7-1.1 2.9-1.7 5.3-1.7s4.6.6 5.3 1.7C14.1 13.9 12.2 15 10 15z"/>
          </svg>
        )}
      </div>
      <div className="msg__body">
        <div className="msg__content">
          {rendered}
          {isStreaming && <span className="cursor-blink">▌</span>}
        </div>
      </div>
    </div>
  )
}
