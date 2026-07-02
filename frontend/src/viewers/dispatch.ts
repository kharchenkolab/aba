/**
 * Client-side viewer dispatch — mirrors core/viewers/registry.py's
 * `viewers_for(node)` so file clicks don't pay a network round-trip
 * per pick. The registry is fetched once at app start and cached;
 * dispatch is then a pure local computation.
 */
import { useEffect, useState } from 'react'
import type { FileNode, ViewerInfo, ViewerRegistryEntry, ViewersResponse } from './types'

// Mirrors backend cheap MIME inference (core/viewers/registry.py).
const IMAGE_BY_EXT: Record<string, string> = {
  '.png':  'image/png',  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.gif':  'image/gif',  '.webp': 'image/webp', '.svg': 'image/svg+xml',
}
const TEXT_BY_EXT: Record<string, string> = {
  '.txt':  'text/plain', '.log':  'text/plain', '.md':   'text/markdown',
}
const APP_BY_EXT: Record<string, string> = {
  '.pdf':  'application/pdf', '.json': 'application/json',
}

function extOf(s: string): string {
  if (!s) return ''
  const m = s.match(/\.[A-Za-z0-9]+$/)
  return m ? m[0].toLowerCase() : ''
}

function mimeMatch(ext: string, patterns: string[]): boolean {
  const mime = IMAGE_BY_EXT[ext] || TEXT_BY_EXT[ext] || APP_BY_EXT[ext]
  if (!mime) return false
  return patterns.some(p => p === mime || (p.endsWith('/*') && mime.startsWith(p.slice(0, -1))))
}

/** Pick applicable viewers for a node — pure function over the cached
 *  registry. Returns viewers sorted by descending priority. */
export function dispatchViewers(node: FileNode, registry: ViewerRegistryEntry[]): ViewerInfo[] {
  const entityType = (node.entity_type || '').toLowerCase()
  const artifact   = node.artifact_path || ''
  const name       = node.name || ''
  const size       = node.size ?? 0
  const sizeKb     = size > 0 ? Math.ceil(size / 1024) : 0
  const ext        = extOf(name) || extOf(artifact)

  const out: ViewerRegistryEntry[] = []
  for (const v of registry) {
    if (v.max_size_kb && sizeKb && sizeKb > v.max_size_kb) continue
    if (v.applies_any) { out.push(v); continue }
    let match = false
    if (entityType && v.entity_types.includes(entityType)) match = true
    // Suffix match so multi-dot extensions (.lstar.zarr) work alongside .h5ad/.png.
    const nameL = (name || artifact).toLowerCase()
    if (v.extensions.some(e => nameL.endsWith(e.toLowerCase()))) match = true
    if (v.mime_patterns.length && mimeMatch(ext, v.mime_patterns)) match = true
    if (match) out.push(v)
  }
  out.sort((a, b) => (b.priority - a.priority) || a.id.localeCompare(b.id))
  return out
}

/** Build the response shape FileCanvas expects (matches the legacy
 *  /api/viewers/for endpoint), using client-side dispatch + a derived
 *  download URL. */
export function dispatchResponse(node: FileNode, registry: ViewerRegistryEntry[]): ViewersResponse {
  const viewers = dispatchViewers(node, registry)
  // Download URL mirrors the backend: prefer path, fall back to entity_id.
  let download_url: string | null = null
  if (node.path) {
    download_url = `/api/files/download?path=${encodeURIComponent(node.path)}`
  } else if (node.entity_id) {
    download_url = `/api/entities/${encodeURIComponent(node.entity_id)}/download`
  }
  return { primary: viewers[0]?.id ?? null, viewers, download_url }
}

// ---- Registry cache ----

let _registryPromise: Promise<ViewerRegistryEntry[]> | null = null

function loadRegistry(): Promise<ViewerRegistryEntry[]> {
  if (!_registryPromise) {
    _registryPromise = fetch('/api/viewers/registry')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`${r.status}`)))
      .catch(e => {
        _registryPromise = null   // allow retry on failure
        throw e
      })
  }
  return _registryPromise
}

/** Hook returning the cached viewer registry, or null while loading.
 *  The fetch happens once per app session. */
export function useViewerRegistry(): ViewerRegistryEntry[] | null {
  const [reg, setReg] = useState<ViewerRegistryEntry[] | null>(null)
  useEffect(() => {
    let cancelled = false
    loadRegistry()
      .then(r => { if (!cancelled) setReg(r) })
      .catch(() => { if (!cancelled) setReg([]) })  // empty on error → NoViewerFallback
    return () => { cancelled = true }
  }, [])
  return reg
}
