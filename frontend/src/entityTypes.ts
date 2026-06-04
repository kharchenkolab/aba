/**
 * Entity-type registry — frontend mirror of backend/core/entity_types/.
 *
 * Fetches the catalog from `/api/entity-types` once on first read +
 * caches in memory. Entity-aware components dispatch via lookups here
 * instead of hardcoded `switch(entity.type)` / `Set([...])` literals,
 * so adding a new bio type means writing a YAML + a panel — no
 * frontend code change in the shell.
 *
 * Phase 4.6 of misc/phase4_entity_types.md.
 */

export interface EntityTypeSpec {
  name: string
  display: string
  icon: string
  hidden: boolean
  category: string | null
  status_states: string[]
  ui: {
    panel?: string
    list_fields?: string[]
    thumbnail?: string | null
  }
  creation: {
    rail_plus?: boolean
    agent_tools?: string[]
    user_gestures_chat?: string[]
    user_gestures_focus?: string[]
  }
  advisors?: {
    on_create?: string[]
    on_pin?: string[]
    on_status_change?: Record<string, string[]>
    /** Frontend auto-triggers /advise after focus for types that opt in.
     *  Today: dataset (Explorer surfaces what's in it) + narrative
     *  (Stylist reviews prose). Other types stay quiet on focus. */
    on_focus_auto?: boolean
  }
}

let _catalog: Record<string, EntityTypeSpec> | null = null
let _inflight: Promise<Record<string, EntityTypeSpec>> | null = null

async function _fetch(): Promise<Record<string, EntityTypeSpec>> {
  const r = await fetch('/api/entity-types')
  if (!r.ok) {
    console.error('entity-types fetch failed', r.status)
    return {}
  }
  const list: EntityTypeSpec[] = await r.json()
  const out: Record<string, EntityTypeSpec> = {}
  for (const t of list) out[t.name] = t
  return out
}

/** Trigger an early load (call once at app startup). Subsequent calls
 *  return the cached map. Subsequent renders that call `typeOf(name)`
 *  see the populated cache. */
export async function loadEntityTypes(): Promise<Record<string, EntityTypeSpec>> {
  if (_catalog) return _catalog
  if (!_inflight) _inflight = _fetch()
  _catalog = await _inflight
  return _catalog
}

/** Synchronous lookup. Returns null if the catalog hasn't loaded yet
 *  (call sites should tolerate that — fall back to permissive defaults). */
export function typeOf(name: string | undefined | null): EntityTypeSpec | null {
  if (!name || !_catalog) return null
  return _catalog[name] ?? null
}

/** True iff the type declares the named user-chat-gesture (pin, claim, …)
 *  in its `creation.user_gestures_chat` list. Permissive default: when the
 *  catalog hasn't loaded yet or the type is unknown, returns false. */
export function typeHasChatGesture(name: string | undefined | null, gesture: string): boolean {
  const t = typeOf(name)
  return !!t?.creation.user_gestures_chat?.includes(gesture)
}

/** True iff the type is in the named category (leaf, run, claim, …). */
export function typeInCategory(name: string | undefined | null, category: string): boolean {
  return typeOf(name)?.category === category
}
