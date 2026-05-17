import React from 'react'
import ReactMarkdown from 'react-markdown'
import { DisplayMessage, Block } from '../types'
import './Message.css'

function toolLabel(name: string) {
  const labels: Record<string, string> = {
    list_data_files: 'listed data files',
    read_csv_info: 'read CSV file',
    run_python: 'ran Python script',
  }
  return labels[name] ?? name
}

function renderBlock(block: Block, i: number) {
  switch (block.type) {
    case 'text':
      return (
        <div key={i} className="msg-text">
          <ReactMarkdown>{block.text}</ReactMarkdown>
        </div>
      )
    case 'image':
      return (
        <div key={i} className="msg-image">
          <img src={block.url} alt={block.alt ?? 'plot'} />
        </div>
      )
    case 'tool_start':
      return (
        <div key={i} className="msg-tool-indicator">
          <span className="tool-spinner" />
          <em>Guide is {toolLabel(block.name)}…</em>
        </div>
      )
    case 'tool_result': {
      const hasError = 'error' in block.result
      return (
        <div key={i} className={`msg-tool-indicator done ${hasError ? 'error' : ''}`}>
          <span className="tool-check">{hasError ? '✗' : '✓'}</span>
          <em>
            {hasError
              ? `Tool error: ${block.result.error}`
              : `Done`}
          </em>
        </div>
      )
    }
    default:
      return null
  }
}

interface Props {
  message: DisplayMessage
  isStreaming?: boolean
}

export default function Message({ message, isStreaming }: Props) {
  const isUser = message.role === 'user'

  return (
    <div className={`msg ${isUser ? 'msg--user' : 'msg--guide'}`}>
      <div className={`msg__avatar ${isUser ? 'msg__avatar--user' : 'msg__avatar--guide'}`}>
        {isUser ? 'PP' : (
          <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
            <path d="M10 2a8 8 0 100 16A8 8 0 0010 2zm0 3a1.5 1.5 0 110 3 1.5 1.5 0 010-3zm0 10c-2.2 0-4.1-1.1-5.3-2.8.7-1.1 2.9-1.7 5.3-1.7s4.6.6 5.3 1.7C14.1 13.9 12.2 15 10 15z"/>
          </svg>
        )}
      </div>
      <div className="msg__body">
        <div className="msg__head">
          <span className={`msg__name ${isUser ? '' : 'msg__name--guide'}`}>
            {isUser ? 'Peter' : 'Guide'}
          </span>
          {!isUser && <span className="msg__badge">APP</span>}
        </div>
        <div className="msg__content">
          {message.blocks.map((b, i) => renderBlock(b, i))}
          {isStreaming && <span className="cursor-blink">▌</span>}
        </div>
      </div>
    </div>
  )
}
