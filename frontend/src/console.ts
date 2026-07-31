/**
 * Console feed store — the single client-side timeline behind the (i) drawer's
 * Console tab.
 *
 * Merges the two event streams the app already receives into one bounded,
 * structured feed:
 *   - per-turn chat SSE (useChat calls noteTurnEvent for every event), and
 *   - the global /api/notifications channel (App calls noteNotification) —
 *     including the backend's structured `console` envelope (substrate
 *     activity relayed from the compute layer: transfers, jobs, kernels,
 *     env ops, chunk streaming).
 *
 * Rows are structured (category / severity / site / verb / facts), not
 * preformatted strings — rendering decides layout, filters compose over
 * facets. A tool call is ONE row that updates in place: tool_start opens it
 * live, progress/chunk events fold into counters, tool_result closes it with
 * status + duration. Framework-free singleton (module state + subscribe),
 * consumed via useSyncExternalStore.
 */
import type { SSEEvent, NotificationEvent } from './wire'

export type Category =
  | 'agent' | 'tool' | 'run' | 'data' | 'env' | 'compute' | 'serve' | 'system'
export type Severity = 'info' | 'warn' | 'error'

export const CATEGORIES: Category[] = [
  'agent', 'tool', 'run', 'data', 'env', 'compute', 'serve', 'system']

export interface ConsoleFacts {
  dur_ms?: number
  bytes?: number
  status?: string
  ref?: string
}

export interface ConsoleRow {
  id: number
  ts: number                 // epoch ms (row creation = event start)
  category: Category
  severity: Severity
  site?: string
  verb: string
  summary?: string
  facts?: ConsoleFacts
  detail?: unknown            // full payload for the expanded view
  live?: boolean              // an open tool call — still updating in place
  updates?: number            // folded progress/chunk ticks
}

const CAP = 2000

let rows: ConsoleRow[] = []
let nextId = 1
const listeners = new Set<() => void>()
/** tool_use_id → row id for open (live) tool calls. */
const openTools = new Map<string, number>()

export function subscribeConsole(fn: () => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

export function consoleRows(): ConsoleRow[] { return rows }

/** Test/reset hook — clears the feed and correlation state. */
export function resetConsole(): void {
  rows = []
  nextId = 1
  openTools.clear()
  emitChange()
}

function emitChange() { listeners.forEach(fn => fn()) }

function push(row: Omit<ConsoleRow, 'id'>): ConsoleRow {
  const r: ConsoleRow = { ...row, id: nextId++ }
  const base = rows.length >= CAP ? rows.slice(rows.length - CAP + 1) : rows
  rows = [...base, r]
  emitChange()
  return r
}

function byId(id: number): ConsoleRow | undefined {
  return rows.find(r => r.id === id)
}

function patch(id: number, up: Partial<ConsoleRow>): void {
  rows = rows.map(r => (r.id === id ? { ...r, ...up } : r))
  emitChange()
}

/** Compact one-line gist of a tool input (full input lives in detail). */
function gist(o: Record<string, unknown>): string {
  try {
    return Object.entries(o || {})
      .map(([k, v]) => `${k}=${String(v).slice(0, 40)}`).join(' ').slice(0, 160)
  } catch { return '' }
}

/** A tool input's `site`/`target` param, when present — the console's
 *  at-a-glance placement signal. */
function siteOf(input: Record<string, unknown>): string | undefined {
  const s = input?.site ?? input?.target_site
  return typeof s === 'string' && s ? s : undefined
}

function statusOf(result: unknown): string | undefined {
  const st = (result as Record<string, unknown>)?.status
  return st == null ? undefined : String(st)
}

// ── producers ────────────────────────────────────────────────────────────────

/** Fold one per-turn SSE event into the feed. */
export function noteTurnEvent(ev: SSEEvent): void {
  const ts = Date.now()
  switch (ev.type) {
    case 'delta':
    case 'usage':
      return                                   // chat text / accounting — not activity
    case 'tool_start': {
      const r = push({
        ts, category: 'tool', severity: 'info', verb: ev.name,
        site: siteOf(ev.input), summary: gist(ev.input),
        detail: { input: ev.input }, live: true,
      })
      openTools.set(ev.tool_use_id, r.id)
      return
    }
    case 'tool_progress': {
      const id = openTools.get(ev.tool_use_id)
      if (id == null) {                        // reattach orphan — still show it
        push({ ts, category: 'tool', severity: 'info', verb: ev.name,
               summary: ev.message ?? ev.phase ?? '', live: true })
        return
      }
      const row = byId(id)
      patch(id, { updates: (row?.updates ?? 0) + 1,
                  summary: ev.message ?? row?.summary })
      return
    }
    case 'tool_chunk': {
      const id = openTools.get(ev.tool_use_id)
      if (id == null) return                   // orphan wire noise — no anchor
      const row = byId(id)
      patch(id, { updates: (row?.updates ?? 0) + 1,
                  facts: { ...row?.facts, bytes: ev.bytes_total } })
      return
    }
    case 'tool_result': {
      const id = openTools.get(ev.tool_use_id)
      openTools.delete(ev.tool_use_id)
      const status = statusOf(ev.result)
      const severity: Severity = status && /error|fail/i.test(status) ? 'error' : 'info'
      if (id == null) {
        push({ ts, category: 'tool', severity, verb: ev.name,
               facts: { status }, detail: { result: ev.result } })
        return
      }
      const row = byId(id)
      patch(id, {
        live: false, severity,
        facts: { ...row?.facts, status, dur_ms: row ? ts - row.ts : undefined },
        detail: { ...(row?.detail as Record<string, unknown>), result: ev.result },
      })
      return
    }
    case 'manifest':
      push({ ts, category: 'agent', severity: 'info', verb: 'turn',
             summary: `turn ${ev.manifest.turn_index}`, detail: ev.manifest })
      return
    case 'plan':
      push({ ts, category: 'agent', severity: 'info', verb: 'plan',
             summary: ev.title || 'plan', detail: ev })
      return
    case 'notice':
      push({ ts, category: 'agent', severity: 'warn', verb: 'notice', summary: ev.text })
      return
    case 'error':
      push({ ts, category: 'agent', severity: 'error', verb: 'error',
             summary: ev.text, detail: ev.detail ?? null })
      return
    case 'cancelled':
      push({ ts, category: 'agent', severity: 'warn', verb: 'cancelled',
             summary: ev.reason || undefined })
      return
    case 'done':
      push({ ts, category: 'agent', severity: 'info', verb: 'turn done' })
      return
    case 'job_submitted':
      push({ ts, category: 'run', severity: 'info', verb: 'job submitted',
             summary: ev.job.title || ev.job.id,
             facts: { ref: ev.job.id, status: ev.job.status || undefined },
             detail: ev.job })
      return
    case 'entity_registered':
      push({ ts, category: 'system', severity: 'info', verb: 'entity registered',
             summary: `${ev.entity.type}: ${ev.entity.title}`,
             facts: { ref: ev.entity.id } })
      return
    case 'clarification_pending':
      push({ ts, category: 'agent', severity: 'warn', verb: 'clarification',
             summary: ev.question })
      return
    case 'approval_pending':
      push({ ts, category: 'agent', severity: 'warn', verb: 'approval',
             summary: `approve ${ev.tool_name}` })
      return
    case 'deferred_tool_pending':
      push({ ts, category: 'agent', severity: 'info', verb: 'deferred',
             summary: ev.tool_name, facts: { ref: ev.deferred_id } })
      return
    case 'suggestion_logged':
      push({ ts, category: 'system', severity: 'info', verb: 'suggestion',
             summary: ev.trigger })
      return
    default:
      push({ ts, category: 'system', severity: 'info',
             verb: (ev as { type: string }).type, detail: ev })
  }
}

/** Fold one /api/notifications event into the feed. */
export function noteNotification(ev: NotificationEvent): void {
  const ts = Date.now()
  switch (ev.type) {
    case 'hello':
      return
    case 'compute':
      // Legacy Settings-tab envelope — the relay double-publishes site kinds
      // as structured `console` events, which is what the feed shows.
      return
    case 'console': {
      const category: Category =
        (CATEGORIES as string[]).includes(ev.category) ? ev.category as Category : 'system'
      const severity: Severity =
        ev.severity === 'error' || ev.severity === 'warn' ? ev.severity : 'info'
      push({
        ts, category, severity,
        site: ev.site ?? undefined, verb: ev.verb,
        summary: ev.summary ?? undefined,
        facts: {
          dur_ms: ev.dur_ms ?? undefined, bytes: ev.bytes ?? undefined,
          status: ev.status ?? undefined, ref: ev.ref ?? undefined,
        },
        detail: ev.detail ?? undefined,
      })
      return
    }
    case 'entity_updated':
      push({ ts, category: 'system', severity: 'info', verb: 'entity updated',
             summary: ev.reason, facts: { ref: ev.entity_id }, detail: ev })
      return
    case 'module':
      push({ ts, category: 'system', severity: ev.error ? 'error' : 'info',
             verb: 'module', summary: `${ev.title} · ${ev.state}`, detail: ev })
      return
  }
}

// ── facet filtering (pure — unit-tested) ─────────────────────────────────────

export interface ConsoleFilter {
  cats: Set<Category> | null     // null = all
  sites: Set<string> | null      // null = all ('' matches site-less rows)
  errorsOnly: boolean
  q: string
}

export function filterRows(all: ConsoleRow[], f: ConsoleFilter): ConsoleRow[] {
  const q = f.q.trim().toLowerCase()
  return all.filter(r => {
    if (f.cats && !f.cats.has(r.category)) return false
    if (f.sites && !f.sites.has(r.site ?? '')) return false
    if (f.errorsOnly && r.severity === 'info') return false
    if (q) {
      const hay = `${r.verb} ${r.summary ?? ''} ${r.site ?? ''} ${r.facts?.status ?? ''}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
}

/** The distinct sites present in the feed (for the site facet chips). */
export function consoleSites(all: ConsoleRow[]): string[] {
  const s = new Set<string>()
  for (const r of all) if (r.site) s.add(r.site)
  return [...s].sort()
}
