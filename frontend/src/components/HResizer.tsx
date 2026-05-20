/**
 * Vertical draggable divider for resizing a side column (e.g. the project
 * tree). Drag left/right; the chevron collapses/restores the column.
 */
import { useEffect, useRef } from 'react'
import './HResizer.css'

interface Props {
  onDrag: (deltaX: number) => void
  onToggle: () => void
  collapsed: boolean
  /** Which side the collapsible panel is on (controls chevron direction). */
  side?: 'left' | 'right'
}

export default function HResizer({ onDrag, onToggle, collapsed, side = 'left' }: Props) {
  const chevron = side === 'left'
    ? (collapsed ? '›' : '‹')
    : (collapsed ? '‹' : '›')
  const lastX = useRef(0)
  const dragging = useRef(false)

  useEffect(() => {
    function move(e: MouseEvent) {
      if (!dragging.current) return
      onDrag(e.clientX - lastX.current)
      lastX.current = e.clientX
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
      className="hresizer"
      onMouseDown={e => {
        dragging.current = true
        lastX.current = e.clientX
        document.body.style.cursor = 'col-resize'
        document.body.style.userSelect = 'none'
      }}
    >
      <div className="hresizer__grip" />
      <button
        className="hresizer__toggle"
        title={collapsed ? 'Show project tree' : 'Hide project tree'}
        onMouseDown={e => e.stopPropagation()}
        onClick={onToggle}
      >
        {chevron}
      </button>
    </div>
  )
}
