/**
 * Typed API client seam (modularity_audit2 Phase 3.4a).
 *
 * Centralizes calls to the backend `/api/` so the platform/components shell
 * doesn't sprinkle inline `fetch()` (the audit's "no API-client seam" finding —
 * 132 inline fetches across the UI). New platform/components code must route
 * through here (enforced by the fetch ratchet in __platform_imports.test.ts);
 * the existing inline-fetch files are grandfathered and burn down into typed
 * helpers below over time.
 *
 * Uses the global `fetch` (which the app patches to carry project_id), so these
 * helpers inherit per-project routing for free.
 */

export class ApiError extends Error {
  status: number
  path: string
  body?: string
  constructor(status: number, path: string, body?: string) {
    super(`API ${status} for ${path}`)
    this.name = 'ApiError'
    this.status = status
    this.path = path
    this.body = body
  }
}

async function _do<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init)
  if (!r.ok) {
    let body: string | undefined
    try { body = await r.text() } catch { /* ignore */ }
    throw new ApiError(r.status, path, body)
  }
  // 204 / empty body → undefined
  const text = await r.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export function apiGet<T = unknown>(path: string): Promise<T> {
  return _do<T>(path)
}

export function apiSend<T = unknown>(path: string, method: 'POST' | 'PATCH' | 'PUT' | 'DELETE',
                                     body?: unknown): Promise<T> {
  return _do<T>(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export const apiPost = <T = unknown>(path: string, body?: unknown) => apiSend<T>(path, 'POST', body)
export const apiPatch = <T = unknown>(path: string, body?: unknown) => apiSend<T>(path, 'PATCH', body)
export const apiDelete = <T = unknown>(path: string) => apiSend<T>(path, 'DELETE')

/** Multipart POST — send a FormData body (the browser sets the multipart
 *  boundary; no Content-Type header). For file uploads / chat attachments.
 *  Throws ApiError on a non-2xx; returns the parsed JSON. */
export function apiUpload<T = unknown>(path: string, form: FormData): Promise<T> {
  return _do<T>(path, { method: 'POST', body: form })
}

// --- Typed helpers for common endpoints (extend as the shell migrates) ---

export interface ProvNode { id: string; type: string; title: string; rel: string; depth: number }
export interface ProvInput {
  ref: string; kind: string; name?: string; title?: string; path?: string
  version?: string; exists?: boolean
}
export interface ProvMethod {
  kind?: string; tool_name?: string; executor?: string; language?: string
  code?: string; code_hash?: string; code_lines?: number; steps?: number
  exec_id?: string; command?: string[] | string
  engine?: { name?: string; version?: string }
  params?: Record<string, unknown>; recipe_id?: string; recipes?: string[]
}
export interface ProvEnvironment {
  language?: string; language_version?: string; env_fingerprint?: string
  package_count?: number; key_packages?: { name: string; version: string }[]
  images?: string[]; backfilled?: boolean
  drift?: { changed?: number; total?: number; moved?: boolean } | null
}
export interface ProvAttribution {
  actor?: string | null; created_at?: string; started_at?: string
  completed_at?: string; wall_time_s?: number; status?: string; seed?: number | null
}
export interface EntityProvenance {
  // Flat keys kept for back-compat with the old panel.
  upstream: ProvNode[]
  downstream: ProvNode[]
  promotion?: { by?: string | null; at?: string | null; from?: string[] | null } | null
  // Rich evidence (prov2): assembled from derivation+actor + the exec record + edges.
  entity?: { id: string; type: string; title?: string }
  method?: ProvMethod
  inputs?: ProvInput[]
  environment?: ProvEnvironment
  attribution?: ProvAttribution
  lineage?: { upstream: ProvNode[]; downstream: ProvNode[] }
  reproducibility?: { has_exec: boolean; reproducible: boolean; backfilled: boolean; revisable: boolean }
}

export function getEntityProvenance(entityId: string): Promise<EntityProvenance> {
  return apiGet<EntityProvenance>(`/api/entities/${encodeURIComponent(entityId)}/provenance`)
}
