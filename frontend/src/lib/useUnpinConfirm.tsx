/**
 * Shared unpin-confirm UX. Mirrors the ResultView ⋯ → "Remove from Result"
 * convention so every surface that exposes an unpin gesture (Run-view
 * pin button, chat FigurePin, future lightbox controls, etc.) gets the
 * same two cases:
 *
 *   1. The wrapping Result has OTHER meaningful members (figure/table/
 *      non-empty note) → destructive ConfirmDialog. Unpin only removes
 *      this member from the Result; the figure entity + revisions stay.
 *
 *   2. The wrapping Result has only this evidence as its non-auto
 *      content → destructive ConfirmDialog that states the consequence:
 *      the now-empty Result is ARCHIVED along with the unpin (that is
 *      the backend's actual /unpin contract — reversible, the caption/
 *      interpretation is preserved on the archived Result). The earlier
 *      block-with-info dead end ("go delete it from the rail ⋯ menu")
 *      left users in a half-toggled state with no completable action
 *      (PK 2026-07-17); a confirm that ACTS is both honest and safe.
 *
 * Usage:
 *
 *   const { requestUnpin, dialog } = useUnpinConfirm(entities, refresh)
 *   …
 *   // somewhere a click handler decides to unpin:
 *   requestUnpin(evidenceEntityId, displayLabel)
 *   …
 *   // mount the dialog (idempotent; renders null when idle):
 *   return <>… {dialog} …</>
 *
 * The hook owns no opinion about WHO drove the pin (chat figure, Run
 * tile, etc.). Callers still issue the pin POST themselves; this hook
 * only mediates the inverse.
 */
import { useState, useMemo, useCallback } from 'react'
import type { Entity } from '../types'
import ConfirmDialog from '../components/ConfirmDialog'


/** Mirrors ResultView._memberIsNonAuto. A member carries
 *  user-meaningful content when it's a pinned figure or table, or a
 *  text note with non-empty text. Empty text notes are placeholders. */
function _memberIsNonAuto(m: { kind: string; text?: string }): boolean {
  if (m.kind === 'figure' || m.kind === 'table') return true
  if (m.kind === 'text') return (m.text ?? '').trim().length > 0
  return false
}


interface UnpinTarget {
  /** Entity id of the evidence (the figure/table being unpinned). */
  evidenceId: string
  /** Display label for the dialog title. */
  label: string
  /** Pre-classified branch — saves a re-walk at render time. */
  mode: 'figure' | 'last'
}


export function useUnpinConfirm(entities: Entity[], onRefresh: () => void) {
  const [target, setTarget] = useState<UnpinTarget | null>(null)

  const entityById = useMemo(() => {
    const m: Record<string, Entity> = {}
    for (const e of entities) m[e.id] = e
    return m
  }, [entities])

  // Find the active Result wrapping this evidence. Pre-PIN-B Results
  // may not carry primary_evidence_id (the backfill hook should have
  // stamped them on project open; we still tolerate the absence and
  // fall back to a members-scan).
  const findWrapping = useCallback((evidenceId: string): Entity | null => {
    for (const e of entities) {
      if (e.type !== 'result' || e.status !== 'active') continue
      const md = (e.metadata as { primary_evidence_id?: string; members?: Array<{ ref?: string }> } | null) ?? {}
      if (md.primary_evidence_id === evidenceId) return e
      const members = md.members ?? []
      if (members.some(m => m.ref === evidenceId)) return e
    }
    return null
  }, [entities])

  // Returns the dialog mode, or null when there's no wrapping Result
  // and the caller should treat the click as a "redundant unpin" no-op.
  const classify = useCallback((evidenceId: string): 'figure' | 'last' | null => {
    const wrapping = findWrapping(evidenceId)
    if (!wrapping) return null
    const md = (wrapping.metadata as { members?: Array<{ kind: string; text?: string; ref?: string }> } | null) ?? {}
    const members = md.members ?? []
    const nonAuto = members.filter(_memberIsNonAuto)
    const thisIsLastNonAuto = nonAuto.length <= 1 &&
      nonAuto.some(m => m.ref === evidenceId)
    return thisIsLastNonAuto ? 'last' : 'figure'
  }, [findWrapping])

  /** Open the appropriate dialog for unpinning `evidenceId`. */
  const requestUnpin = useCallback((evidenceId: string, label: string): void => {
    const mode = classify(evidenceId)
    if (!mode) {
      // No active wrapping Result — fall through to a direct unpin so
      // the user isn't stuck if the entities prop is mid-refresh.
      _commit(evidenceId)
      return
    }
    setTarget({ evidenceId, label, mode })
  }, [classify])

  // Run the actual unpin POST and clear the dialog state.
  function _commit(evidenceId: string): void {
    fetch(`/api/entities/${encodeURIComponent(evidenceId)}/unpin`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    })
      .catch(() => {})
      .finally(() => {
        setTarget(null)
        onRefresh()
      })
  }

  // Look up the entity name we should show in the title when the
  // caller didn't pass an explicit label. Falls back to "this figure".
  const labelFor = (t: UnpinTarget): string =>
    t.label || entityById[t.evidenceId]?.title || 'this figure'

  const dialog = target && target.mode === 'last'
    ? <ConfirmDialog
        title={`Unpin "${labelFor(target)}"?`}
        variant="destructive" primaryLabel="Unpin & archive Result"
        onPrimary={() => _commit(target.evidenceId)}
        onCancel={() => setTarget(null)}
        body={
          <>
            <p>
              <strong>{labelFor(target)}</strong> is the only content in the
              Result that wraps it, so unpinning also <strong>archives that
              Result</strong> (its caption and notes are preserved on the
              archived copy — this can be undone from archived items).
            </p>
            <p>
              The figure itself (and any revisions) stay in the project.
            </p>
          </>
        }
      />
    : target && target.mode === 'figure'
      ? <ConfirmDialog
          title={`Unpin "${labelFor(target)}"?`}
          variant="destructive" primaryLabel="Unpin"
          onPrimary={() => _commit(target.evidenceId)}
          onCancel={() => setTarget(null)}
          body={
            <>
              <p>
                <strong>{labelFor(target)}</strong> will be removed from the
                Result that includes it. The figure itself (and any revisions)
                stay in the project — only the Result-membership is dropped.
              </p>
            </>
          }
        />
      : null

  return { requestUnpin, dialog }
}
