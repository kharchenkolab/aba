/**
 * External-viewer launch (viewers.md §3 'external' mode).
 *
 * An external viewer names an `open_external` launcher server-side. Clicking
 * it POSTs to /api/viewers/launch, which returns a URL (and optionally a
 * background prepare job); we open that URL in a new window. The fetch shim
 * (oodBase) doesn't rewrite arbitrary launcher paths, so prepend the OOD base
 * explicitly before window.open.
 */
import { withBasePath } from '../oodBase'
import type { FileNode, ViewerInfo } from './types'

export interface LaunchResponse {
  url: string
  prepare_job_id: string | null
  label: string | null
}

/** Ask the backend to resolve an external viewer to a URL, then open it in a
 *  new window. Returns the launch response (for callers that want the job id). */
export async function launchExternal(node: FileNode, viewer: ViewerInfo): Promise<LaunchResponse> {
  const r = await fetch('/api/viewers/launch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      viewer_id: viewer.id,
      ...(node.path ? { path: node.path } : { entity_id: node.entity_id }),
    }),
  })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(d?.detail || `launch failed (${r.status})`)
  const url = withBasePath(String(d.url))
  window.open(url, `viewer-${viewer.id}`, 'popup=yes,width=1400,height=900')
  return d as LaunchResponse
}
