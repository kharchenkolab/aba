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

/** Full viewer entry from /api/viewers/registry — includes the matching
 *  metadata so we can dispatch client-side without a per-click API call. */
export interface ViewerRegistryEntry extends ViewerInfo {
  extensions:    string[]
  mime_patterns: string[]
  entity_types:  string[]
  applies_any:   boolean
  max_size_kb:   number | null
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
  ephemeral?: boolean              // working/scratch tier — uncurated, GC-able until promoted
  note?: string                    // folder subtitle (e.g. the working/ scratch note)
  // Durability (output_durability.md §6.2): set on file nodes from /api/runs/{id}/durable.
  state?: string | null            // kept | pinned-pending | in-sandbox | cleared
  badge?: string | null            // human label for the durable state
  large?: boolean                  // > harvest cap — durable via weft retention, not aba store
  site?: string | null             // site holding the retained bytes (remote in-place)
  art_kind?: string                // artifact kind: figure | table | file
}

/** A viewer component receives the node + the picked ViewerInfo and renders. */
export interface ViewerComponentProps {
  node: FileNode
  viewer: ViewerInfo
  onFocus?: (id: string) => void
  onClose?: () => void
}

export type ViewerComponent = (props: ViewerComponentProps) => ReactNode
