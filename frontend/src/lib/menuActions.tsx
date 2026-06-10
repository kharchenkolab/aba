/**
 * Entity-menu action registry — bio side of the platform/bio split.
 *
 * The platform shell (EntityMenu) opens a small "⋯" popover with actions
 * that depend on what the entity IS (a figure can be pinned-into-a-Result;
 * a Result cascades members on delete; a dataset folders into the FS).
 * Those policies are biology-flavored, so the per-type rules live here.
 *
 * The shell calls:
 *   `entity_menu_traits(type) -> Traits`
 * to learn whether the type is pinnable, cascades on delete, has a
 * dataset-style delete confirm, etc. Types that haven't been registered
 * fall back to a conservative `EMPTY_TRAITS` so unknown types don't
 * explode (and a fresh Yaml drop-in needs only an opt-in registration
 * to enable richer affordances).
 */

export interface EntityMenuTraits {
  /** True iff the type supports "Pin this artifact into a Result" — the
   *  primary curation gesture. Maps to the `pin` user-chat-gesture in
   *  entity_types/*.yaml; we keep it here as the fallback for the brief
   *  window before the catalog has loaded. */
  pinnable: boolean
  /** True iff delete should cascade to members + their revision chains
   *  (Result behavior). */
  cascadeMembers: boolean
  /** Delete-confirm body variant. 'dataset' shows file count + size,
   *  'result' shows member cascade summary, 'generic' a plain warning. */
  deleteVariant: 'dataset' | 'result' | 'generic'
}

const EMPTY_TRAITS: EntityMenuTraits = {
  pinnable: false,
  cascadeMembers: false,
  deleteVariant: 'generic',
}

const _MAP = new Map<string, EntityMenuTraits>()

/** Register (or override) the menu traits for an entity type. */
export function register_menu_traits(type: string, traits: Partial<EntityMenuTraits>): void {
  _MAP.set(type, { ...EMPTY_TRAITS, ...traits })
}

/** Look up the menu traits for an entity type. Always returns a value —
 *  unknown types fall back to a conservative empty trait set. */
export function entity_menu_traits(type: string): EntityMenuTraits {
  return _MAP.get(type) ?? EMPTY_TRAITS
}

/** True iff a type is pinnable per the registered traits. Convenience
 *  for EntityMenu, which calls this in render. */
export function is_pinnable(type: string): boolean {
  return entity_menu_traits(type).pinnable
}


// ---------- Default bio menu traits ----------
// Pinnable types mirror the legacy `_PINNABLE_FALLBACK` set in EntityMenu
// (figure / table / cell / note / narrative) — the artifact-layer types
// the user pin-promotes into the Result curation layer.

register_menu_traits('figure',    { pinnable: true })
register_menu_traits('table',     { pinnable: true })
register_menu_traits('cell',      { pinnable: true })
register_menu_traits('note',      { pinnable: true })
register_menu_traits('narrative', { pinnable: true })

// Results: cascade-delete (members + revision chains, both with their own
// detach-or-delete logic on the server) + a Result-specific confirm body.
register_menu_traits('result',    { cascadeMembers: true, deleteVariant: 'result' })

// Datasets get their own confirm body (file count + size).
register_menu_traits('dataset',   { deleteVariant: 'dataset' })
