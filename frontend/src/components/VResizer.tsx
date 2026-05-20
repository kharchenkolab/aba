/**
 * Horizontal draggable divider between the focus canvas and the chat.
 * Drag to resize freely; two chevron tabs maximize either side:
 *   ▴ maximize the figure (collapse chat)   ▾ maximize the chat (collapse figure)
 * Clicking the chevron of the side that's already maximized restores the split.
 */
import { useEffect, useRef } from 'react'
import './VResizer.css'

type VState = 'figure' | 'chat' | 'mid'

interface Props {
  onDrag: (deltaY: number) => void
  onMaxFigure: () => void
  onMaxChat: () => void
  onRestore: () => void
  state: VState
}

export default function VResizer({ onDrag, onMaxFigure, onMaxChat, onRestore, state }: Props) {
  const lastY = useRef(0)
  const dragging = useRef(false)

  useEffect(() => {
    function move(e: MouseEvent) {
      if (!dragging.current) return
      onDrag(e.clientY - lastY.current)
      lastY.current = e.clientY
    }
    function up() {
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
  }, [onDrag])

  return (
    <div
      className="vresizer"
      onMouseDown={e => {
        dragging.current = true
        lastY.current = e.clientY
        document.body.style.cursor = 'row-resize'
        document.body.style.userSelect = 'none'
      }}
    >
      <div className="vresizer__grip" />
      <div className="vresizer__tabs" onMouseDown={e => e.stopPropagation()}>
        <button
          className={`vresizer__tab ${state === 'figure' ? 'is-active' : ''}`}
          title={state === 'figure' ? 'Restore split' : 'Maximize figure'}
          onClick={state === 'figure' ? onRestore : onMaxFigure}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12 7l7 9H5z" /></svg>
        </button>
        <button
          className={`vresizer__tab ${state === 'chat' ? 'is-active' : ''}`}
          title={state === 'chat' ? 'Restore split' : 'Maximize chat'}
          onClick={state === 'chat' ? onRestore : onMaxChat}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12 17L5 8h14z" /></svg>
        </button>
      </div>
    </div>
  )
}
