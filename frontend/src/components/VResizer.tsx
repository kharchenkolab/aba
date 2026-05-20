/**
 * Horizontal draggable divider between the focus canvas and the chat.
 * Drag to resize; the chevron collapses/restores the focus panel so the
 * chat can take most of the height.
 */
import { useEffect, useRef } from 'react'
import './VResizer.css'

interface Props {
  onDrag: (deltaY: number) => void
  onToggle: () => void
  collapsed: boolean
}

export default function VResizer({ onDrag, onToggle, collapsed }: Props) {
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
      <button
        className="vresizer__toggle"
        title={collapsed ? 'Expand focus panel' : 'Collapse focus panel (maximize chat)'}
        onMouseDown={e => e.stopPropagation()}
        onClick={onToggle}
      >
        {collapsed ? '▾' : '▴'}
      </button>
    </div>
  )
}
