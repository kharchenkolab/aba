/**
 * Module-level ref for the multi-member Result viewport pick.
 *
 * When a Result has more than one panel, ResultView's IntersectionObserver
 * decides which panel is "in view" and records it here. useChat reads it
 * at send time and includes it as `focus_member_id` in the chat payload,
 * so the agent's Result focus card can anchor on the right panel for
 * "this plot" / "this table" gestures.
 *
 * Why a module ref rather than React state lifted to App:
 *   - The signal is internal to ResultView (a deep child via the
 *     focus-view registry). Threading callback props up through
 *     FocusCanvas → ResultViewAdapter would pollute every focus view
 *     with a hook only one of them needs.
 *   - useChat needs to read it at send time, not subscribe to changes.
 *     A module ref is cheaper than a context for a read-once signal.
 *
 * Stale-ref guard: every getter requires the matching result id, so a
 * stale value from a previously-focused Result can't leak into a new
 * Result's first message. ResultView calls `clearActiveMember` on
 * unmount or focus change.
 */

let _entry: { resultId: string; memberId: string | null } | null = null


/** Record the current viewport pick for a given Result. memberId=null
 *  is valid (meaning "this Result is focused but no panel has been
 *  picked yet") and is treated the same as "no signal" by getters. */
export function setActiveMember(resultId: string, memberId: string | null): void {
  if (!resultId) return
  _entry = { resultId, memberId }
}


/** Return the current pick if it's for the supplied Result; null
 *  otherwise. The result-id gate is the safety belt that keeps a
 *  stale pick from leaking across navigations. */
export function getActiveMember(forResultId: string): string | null {
  if (!_entry || !forResultId) return null
  if (_entry.resultId !== forResultId) return null
  return _entry.memberId
}


/** Drop the recorded pick. Pass `forResultId` to clear only when the
 *  currently-recorded pick belongs to that Result (idempotent + safe
 *  to call from a stale unmount effect). Omit to clear unconditionally. */
export function clearActiveMember(forResultId?: string): void {
  if (!forResultId) {
    _entry = null
    return
  }
  if (_entry && _entry.resultId === forResultId) {
    _entry = null
  }
}
