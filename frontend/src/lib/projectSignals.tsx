/**
 * Project-signal helpers — bio side of the platform/bio split.
 *
 * The shell's project-entry framing + chat/rail behavior reads a few
 * "is the project in state X" signals (does it have a dataset, a
 * downstream output, a pinned figure, …). Those signals are bio
 * decisions; the shell calls these helpers instead of inspecting
 * entity types directly.
 *
 * Two related families ride along here for the same reason:
 *   • `kept_message_keys(entities)`    — chat-pin state derived from
 *     Note entities (source_key).
 *   • `pinned_figure_ids(entities)`    — figure-pin state derived
 *     from Result members (kind='figure').
 *   • `default_pin_kind()`             — what to pin as when the
 *     caller didn't specify (run output → figure today).
 *
 * Each is a single function so the shell can swap to a different bio
 * domain without restructuring its prop wiring.
 */
import type { Entity } from '../types'

/** Number of dataset entities. Drives "mid-session: first dataset
 *  reveals the Data tab" + the workspace landing copy. */
export function dataset_count(entities: Entity[]): number {
  return entities.filter(e => e.type === 'dataset').length
}

/** True iff the project has any dataset at all. The cold-start orient
 *  effect's gate ("no data → don't ask Guide to orient"). */
export function has_any_dataset(entities: Entity[]): boolean {
  return entities.some(e => e.type === 'dataset')
}

/** True iff the project has at least one pinned figure (= an active
 *  Result entity with a figure member). Drives the right-rail
 *  reveal at project entry. */
export function has_pinned_figure(entities: Entity[]): boolean {
  return entities.some(e =>
    e.type === 'result' &&
    Array.isArray(e.metadata?.members) &&
    (e.metadata!.members as Array<{ kind?: string }>).some(m => m.kind === 'figure'))
}

/** True iff at least one thread carries a user-supplied question.
 *  Pairs with `has_pinned_figure` in the project-entry rail framing. */
export function has_user_question(entities: Entity[]): boolean {
  return entities.some(e =>
    e.type === 'thread' && e.metadata?.question_source === 'user')
}

/** Keys of currently-kept message notes (chat-pin state for the chat
 *  pane). A message is "kept" iff a note entity exists with that
 *  source_key + status='active'. */
export function kept_message_keys(entities: Entity[]): Set<string> {
  return new Set(
    entities
      .filter(e => e.type === 'note' && e.status === 'active'
                   && (e.metadata?.source_key as string))
      .map(e => e.metadata!.source_key as string),
  )
}

/** Entity-ids of figures currently pinned via active Result membership.
 *  Frontend uses this to light the chat figure pin button — see
 *  task #318 (pinned-flag → membership migration). */
export function pinned_figure_ids(entities: Entity[]): Set<string> {
  return new Set<string>(
    entities
      .filter(e => e.type === 'result' && e.status === 'active')
      .flatMap(e => ((e.metadata?.members as Array<{ kind?: string; ref?: string }>) ?? [])
        .filter(m => m.kind === 'figure' && m.ref)
        .map(m => m.ref as string)),
  )
}

/** Default member-kind when the caller didn't specify one for a run
 *  output (pinRunOutput in App.tsx). Today's bio answer is 'figure';
 *  a future domain could land on a different default. */
export function default_pin_kind(): string {
  return 'figure'
}

/** True iff focusing this entity should open a claim-specific route
 *  (App.tsx's goToEntity router calls openClaim for claims, openEntity
 *  for everything else). */
export function uses_claim_focus_route(type: string | undefined | null): boolean {
  return type === 'claim'
}

/** True iff a focused entity wants the freehand-highlight surface
 *  enabled in the canvas-actions row. Today Result is the only
 *  focused type that hosts its own per-MemberPanel highlights
 *  (Figures get their own AnnotatedFigure on focus). */
export function supports_focused_highlighting(type: string | undefined | null): boolean {
  return type === 'result'
}
