/**
 * Shared types for the file-viewer subsystem (viewers.md §5).
 */
import type { ReactNode } from 'react'

/** Mirror of core.viewers.registry.Viewer (the wire shape from /api/viewers/for). */
export interface ViewerInfo {
  id: string
  mode: 'canvas' | 'modal' | 'external'
  component: string | null
  open_external: string | null
  label: string
  priority: number
  requires_consent: boolean
}

export interface ViewersResponse {
  primary: string | null
  viewers: ViewerInfo[]
  download_url: string | null
}

/** A tree node from /api/files/tree (subset we need for viewer dispatch). */
export interface FileNode {
  kind: 'root' | 'folder' | 'file' | 'readme'
  name: string
  path: string
  entity_id?: string | null
  entity_type?: string | null
  title?: string | null
  artifact_path?: string | null
  size?: number | null
  mtime?: number | null
  content?: string                 // readme inline content
  synthesized?: boolean
  synthesized_kind?: string
  synthesized_content?: string
}

/** A viewer component receives the node + the picked ViewerInfo and renders. */
export interface ViewerComponentProps {
  node: FileNode
  viewer: ViewerInfo
  onFocus?: (id: string) => void
  onClose?: () => void
}

export type ViewerComponent = (props: ViewerComponentProps) => ReactNode
