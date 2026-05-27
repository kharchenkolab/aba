/**
 * Viewer component registry (viewers.md §5.2).
 *
 * Maps `component:` names from bio/viewers/registry.yaml to actual
 * React components. The dispatcher (FileCanvas) picks the best
 * applicable canvas-mode viewer whose component is registered here.
 *
 * Adding a new viewer = add a YAML entry on the backend + register
 * the matching React component here. The YAML's `component:` field
 * is the lookup key.
 */
import type { ViewerComponent } from './types'
import MarkdownCanvas from './MarkdownCanvas'
import TextCanvas from './TextCanvas'
import CodeCanvas from './CodeCanvas'
import ImageCanvas from './ImageCanvas'

export const VIEWERS: Record<string, ViewerComponent> = {
  MarkdownCanvas,
  TextCanvas,
  CodeCanvas,
  ImageCanvas,
  // The entity-typed viewers (FigureView, ClaimView, ResultView, etc.)
  // still live inside FocusCanvas today; clicking an entity-backed
  // file routes through onFocus(entity_id) as before. V2 will hoist
  // those into this registry too.
}

export function hasViewer(name: string | null | undefined): boolean {
  return !!name && name in VIEWERS
}
