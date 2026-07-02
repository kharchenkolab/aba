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
  set_local_storage?: Record<string, string> | null
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
  // Seed origin-shared localStorage before opening (e.g. point pagoda3's copilot
  // at ABA's proxy). Root-relative values get the OOD base prefix; the viewer
  // window shares this origin, so it reads the same keys.
  if (d.set_local_storage) {
    for (const [k, v] of Object.entries(d.set_local_storage as Record<string, string>)) {
      try { localStorage.setItem(k, v.startsWith('/') ? withBasePath(v) : v) } catch { /* ignore */ }
    }
  }
  const url = withBasePath(String(d.url))
  // No window features → opens as a new TAB (a features string forces a popup
  // window). The per-viewer name means re-launching reuses the same tab.
  window.open(url, `viewer-${viewer.id}`)
  return d as LaunchResponse
}
