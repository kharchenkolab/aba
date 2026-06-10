/**
 * Entity-classification registry — bio side of the platform/bio split.
 *
 * Several shell call sites need to know whether an entity belongs to a
 * BIO category — "is this an artifact (figure/table/result/note/narrative)",
 * "is this a downstream output of an analysis (which the project-entry
 * framing reveals the right rail for)". Those categories ARE biology; the
 * shell asks `entities_in_class(type, 'artifact')` rather than carrying
 * its own list of type names.
 *
 * Classes are static — declared at module load time, not user-extensible
 * mid-session. That's enough for the shell's needs (the framing rules
 * are stable; bio decides the membership).
 */

const _CLASSES = new Map<string, Set<string>>()

/** Register (or extend) a class with one or more type names. */
export function register_entity_class(className: string, ...types: string[]): void {
  let set = _CLASSES.get(className)
  if (!set) { set = new Set(); _CLASSES.set(className, set) }
  for (const t of types) set.add(t)
}

/** True iff `type` is in the named class. */
export function type_in_class(type: string | undefined | null, className: string): boolean {
  if (!type) return false
  return _CLASSES.get(className)?.has(type) ?? false
}

/** All registered type names for the named class (a fresh array — caller
 *  can mutate without breaking the registry). */
export function types_in_class(className: string): string[] {
  return [...(_CLASSES.get(className) ?? [])]
}


// ---------- Default bio classes ----------
//
// 'artifact'        — leaf bio entities that ride inside a Result (and
//                     contribute to the Files-tab projection: things with
//                     an artifact_path that are user-relevant).
// 'downstream'      — what counts as "downstream output of a Run/Analysis"
//                     for the empty-project framing heuristic: any of these
//                     + the artifact set, plus claim (which is also a
//                     downstream conceptual output).
// 'pinnable_member' — kinds that can appear AS a `members[].kind` value of
//                     a Result (used to derive "pinned" status from
//                     membership instead of an entity flag).
// 'bulk_de_method'  — bulk-DE-eligible types: only used by the shell when
//                     surfacing the "bulk DE method picker" affordance; the
//                     ACTUAL gate is the recipe catalog, not this list.

register_entity_class(
  'artifact',
  'figure', 'table', 'result', 'note', 'narrative',
)

register_entity_class(
  'downstream',
  'figure', 'table', 'result', 'note', 'narrative', 'analysis', 'claim',
)

register_entity_class(
  'pinnable_member',
  'figure', 'table', 'value', 'text',
)
