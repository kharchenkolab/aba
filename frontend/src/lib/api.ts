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

/** Rename an entity. Threads have their own route; every other entity is a
 *  generic PATCH. (Projects go through renameProject — they are not entities.) */
export function renameEntity(id: string, type: string, title: string): Promise<unknown> {
  const path = type === 'thread'
    ? `/api/threads/${encodeURIComponent(id)}`
    : `/api/entities/${encodeURIComponent(id)}`
  return apiPatch(path, { title })
}

/** Rename the project. The backend syncs BOTH the registry entry (the Home
 *  project list) AND the in-project workspace-entity title (what the header
 *  shows), so the two never diverge regardless of where the rename came from. */
export function renameProject(pid: string, name: string): Promise<unknown> {
  return apiPatch(`/api/projects/${encodeURIComponent(pid)}`, { name })
}

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

// --- Settings → Compute (misc/compute_settings.md §4–§7) ---

export interface GpuEntry { model?: string | null; count?: number }
export interface PartitionRow {
  name: string; selected?: boolean; gpus_per_node?: number
  nodes?: number | null; cpus_per_node?: number | null
  mem_gb_per_node?: number | null; max_walltime?: string | null
}
export interface SiteCaps {
  cpus?: number; mem_gb?: number; arch?: string; glibc?: string
  internet?: boolean; module_system?: boolean; probed_at?: number
  gpus?: GpuEntry[]
  scheduler?: { type?: string; version?: string; partitions?: Record<string, unknown>[] }
  storage?: { free_gb?: number; candidates?: { path: string; writable?: boolean; free_gb?: number }[] }
}
export interface StoragePath { path: string; stable?: boolean }
export interface AbaSiteKeys {
  contract?: string; use_for?: string[]; storage?: StoragePath[]
  durable?: boolean
}
export interface VerifyState {
  state?: 'running' | 'done'; ok?: boolean; failed?: string[]
  partitions?: Record<string, { ok?: boolean; note?: string }> | string[] | null
  error?: { detail?: string }
}
export interface ComputeSite {
  name: string; kind: string; health?: string
  cpus?: number; mem_gb?: number; gpus?: number
  scheduler?: string; internet?: boolean
  config?: { root?: string; host?: string; user?: string
             durable?: boolean | string
             policy?: { partitions_allowed?: string[]; notes?: string[]
                        storage?: Record<string, string> } }
  capabilities?: SiteCaps | null
  probed_at?: number
  aba?: AbaSiteKeys
  verify?: VerifyState
}
export interface WorkingOption {
  root: string; free_gb?: number | null; kind: string; note: string
}
export interface ComputeProposal {
  kind: string; machine_type: string; headline: string; name: string
  use_for: string[]
  notes?: string[]
  durable?: boolean
  durable_path?: string | null
  working: { root: string; free_gb?: number | null; reason?: string
             kind?: string; options?: WorkingOption[] }
  long_term: StoragePath[]
  contract: string; contract_evidence?: string[]
  partitions: PartitionRow[]
  account?: string | null; accounts?: string[]
  gpus?: GpuEntry[]
  totals?: { nodes: number; cores: number; gpus: number; partitions: number } | null
  facts?: Record<string, unknown>
}
export interface PreflightResult {
  case: 'ok' | 'auth' | 'hostkey' | 'dns' | 'network' | 'unknown'
  cause?: string; stderr?: string
  hostkey?: { line: string; fingerprint: string; keytype: string }
}
export interface SshTarget { dest: string; port?: number | null; ssh_opts?: string[] }

const cname = (n: string) => encodeURIComponent(n)

export const computeApi = {
  status: () => apiGet<{ ok: boolean; detail: string; self_service?: boolean }>('/api/compute/status'),
  sites: () => apiGet<{ sites: ComputeSite[] }>('/api/compute/sites'),
  site: (name: string) => apiGet<ComputeSite>(`/api/compute/sites/${cname(name)}`),
  load: (name: string) =>
    apiGet<{ start_estimate?: string | null; partitions?: unknown }>(
      `/api/compute/sites/${cname(name)}/load?estimate=true`),
  footprint: (name: string) =>
    apiGet<{ free_bytes?: number; prefixes_bytes?: number; package_cache_bytes?: number }>(
      `/api/compute/sites/${cname(name)}/footprint`),
  hosts: () => apiGet<{ hosts: { host: string; hostname?: string; user?: string }[] }>('/api/compute/hosts'),
  templates: () => apiGet<{ templates: { name: string; dest?: string; note?: string }[] }>('/api/compute/templates'),
  preflight: (t: SshTarget) => apiPost<PreflightResult>('/api/compute/preflight', t),
  acceptHostkey: (line: string) => apiPost<{ ok: boolean }>('/api/compute/hostkey', { line }),
  keysetup: (t: SshTarget) =>
    apiPost<{ ok: boolean; created: boolean; command: string }>('/api/compute/keysetup', t),
  probe: (t: SshTarget) =>
    apiPost<{ capabilities: SiteCaps; proposal: ComputeProposal }>('/api/compute/probe', t),
  connect: (t: SshTarget & { proposal: ComputeProposal }) =>
    apiPost<{ site: string; verifying: boolean }>('/api/compute/sites', t),
  verify: (name: string) => apiPost<{ started: boolean }>(`/api/compute/sites/${cname(name)}/verify`),
  reprobe: (name: string) => apiPost<{ site: string }>(`/api/compute/sites/${cname(name)}/reprobe`),
  edit: (name: string, body: { use_for?: string[]; long_term?: StoragePath[]
                               notes?: string[]; working_root?: string
                               durable?: boolean; durable_path?: string }) =>
    apiPatch<{ site: string }>(`/api/compute/sites/${cname(name)}`, body),
  disconnect: (name: string) => apiDelete<{ site: string }>(`/api/compute/sites/${cname(name)}`),
  gc: (name: string, confirm: boolean) =>
    apiPost<{ reclaimable_bytes?: number; freed_bytes?: number }>(
      `/api/compute/sites/${cname(name)}/gc`, { confirm }),
  advanced: (site?: string) =>
    apiGet<{ available: boolean; url?: string | null }>(
      `/api/compute/advanced${site ? `?site=${cname(site)}` : ''}`),
  // §2 (more_weft_ui.md): what lives only on this machine — feeds every
  // consequence card (Disconnect / durable-uncheck / Free up previews).
  holdings: (name: string) => apiGet<SiteHoldings>(`/api/compute/sites/${cname(name)}/holdings`),
}

export interface SiteHoldings {
  site: string
  kept_runs: number
  kept_bytes: number
  dataset_homes: { entity_id: string; title?: string | null; path?: string | null }[]
  at_risk_if_gone: number
  /** retention index unreachable — kept counts are NOT assessable (zeros lie) */
  unknown?: boolean
  note?: string
}
