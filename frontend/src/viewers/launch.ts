/**
 * External-viewer launch (viewers.md §3 'external' mode).
 *
 * Clicking an external viewer opens ABA's own loading tab (`/viewer-launch`)
 * *synchronously* on the user gesture — so it's never popup-blocked. That page
 * starts the background prepare job, shows friendly progress, and redirects
 * itself to the viewer once the store is ready (or shows an error with Retry +
 * Report). The viewer therefore only ever loads a ready store.
 */
import { withBasePath } from '../oodBase'
import type { FileNode, ViewerInfo } from './types'

/** Current project id from the ABA route (/p/<pid>/…) — the loading tab is a
 *  fresh page with no pinned project context, so we pass it explicitly. */
function currentProjectId(): string {
  const m = location.pathname.match(/\/p\/([^/]+)/)
  return m ? m[1] : ''
}

/** Open the ABA loading tab for an external viewer. Synchronous (opens on the
 *  click gesture); the loading page does the launch + poll + redirect. */
export function launchExternal(node: FileNode, viewer: ViewerInfo): void {
  const params = new URLSearchParams({ viewer: viewer.id, project: currentProjectId() })
  if (viewer.label) params.set('label', viewer.label)
  if (node.path) params.set('path', node.path)
  else if (node.entity_id) params.set('entity', node.entity_id)
  const url = withBasePath('/viewer-launch') + '?' + params.toString()
  window.open(url, `viewer-${viewer.id}`)   // new tab
}
