/**
 * RevisionStrip — controls rendered BELOW a figure/table panel to drive
 * its revision chain (wasRevisionOf graph).
 *
 * Two responsibilities:
 *   1. Manage which revision is currently displayed in the parent — the
 *      `useFigureRevisions` hook tracks displayedId, defaults to the
 *      LATEST revision in the chain, and exposes the displayed entity
 *      so the parent renders the right artifact.
 *   2. Render the control strip:
 *        ‹  rev N of M (not latest)  ›    [💬 Chat ▾]
 *      The SplitButton's dropdown carries Revise / Reproduce. Revise
 *      from a non-latest revision triggers a confirmation dialog
 *      explaining the supersession; on confirm, the chat prefill
 *      tells the agent to call make_revision with supersede_newer=True.
 *
 * Replaces the previous floating-overlay RevisionChevrons (the user
 * clarified 2026-06-07 that figures/tables/cells are NOT user-focus
 * destinations — the Result IS the entity; chevrons belong as a sibling
 * control strip below the image, not as an absolute-positioned overlay
 * that fights with click-to-zoom).
 */
import { useEffect, useState } from 'react'
import type { Entity } from '../types'
import SplitButton from '../components/SplitButton'
import ConfirmDialog from '../components/ConfirmDialog'
import './RevisionStrip.css'


interface RevisionsResponse {
  chain: Entity[]
  position: number
  prev: string | null
  next: string | null
}


/** Fetch the revision chain for `anchorId`. Returns null while loading
 *  or on error — callers should hide chevrons when null.
 *
 *  `refreshKey` is an optional bumpable counter that forces a re-fetch
 *  when its value changes. The parent feeds it from a signal derived
 *  from the global entities list (e.g. count of revisions in the
 *  project) so that when the agent creates a new revision in the
 *  background and the SSE-driven entities refresh fires, this hook
 *  picks up the new chain WITHOUT a page reload. Without this, the
 *  hook's fetch keyed only on `anchorId` would never re-run for the
 *  lifetime of the panel.
 */
export function useFigureHistory(anchorId: string, refreshKey: number = 0): RevisionsResponse | null {
  const [data, setData] = useState<RevisionsResponse | null>(null)
  useEffect(() => {
    if (!anchorId) { setData(null); return }
    let cancelled = false
    fetch(`/api/entities/${encodeURIComponent(anchorId)}/revisions`)
      .then(r => (r.ok ? r.json() : Promise.reject(r)))
      .then((body: RevisionsResponse) => { if (!cancelled) setData(body) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [anchorId, refreshKey])
  return data
}


/** Display + navigation state for the revision chain rooted at `anchorId`.
 *  Defaults the displayed entity to the LATEST revision (chain[0]).
 *  Re-snaps to latest if the chain head changes (e.g. a new revision
 *  was added by the agent in the background — caller is expected to
 *  bump `refreshKey` when entities mutate, see useFigureHistory). */
export function useFigureRevisions(anchorId: string, refreshKey: number = 0) {
  const hist = useFigureHistory(anchorId, refreshKey)
  const chain = hist?.chain ?? []
  const [displayedId, setDisplayedId] = useState<string>(anchorId)
  const headId = chain[0]?.id
  useEffect(() => {
    if (headId) setDisplayedId(headId)
  }, [headId, chain.length])

  const pos = chain.findIndex(e => e.id === displayedId)
  const safePos = pos < 0 ? 0 : pos
  const total = chain.length
  const displayed = pos >= 0 ? chain[pos] : null
  const isLatest = safePos === 0
  return {
    hist, chain, displayedId, displayed,
    pos: safePos, total, isLatest,
    setDisplayedId,
    canGoPrev: safePos < total - 1, // older direction
    canGoNext: safePos > 0,         // newer direction
    goPrev: () => { if (safePos < total - 1) setDisplayedId(chain[safePos + 1].id) },
    goNext: () => { if (safePos > 0) setDisplayedId(chain[safePos - 1].id) },
  }
}


/** Action emitted from RevisionStrip — mirrors the App-level FigureAction
 *  union. The parent forwards these to chatAboutResult / onChatResult. */
export type RevisionAction = 'chat' | 'revision' | 'revision-supersede' | 'reproduce'


interface StripProps {
  rev: ReturnType<typeof useFigureRevisions>
  /** Called when the user picks an action. Carries the displayed entity
   *  so the parent can include its id + url in the chat prefill. */
  onAction: (action: RevisionAction, displayed: Entity) => void
  /** Hide the chevron group (e.g. when the parent provides its own
   *  navigation, like the FocusCanvas header's history clock). */
  hideChevrons?: boolean
  /** Hide the SplitButton (e.g. when the parent has its own action bar). */
  hideActions?: boolean
  /** When the canonical artifact differs from what the panel renders
   *  (e.g. a PDF figure shown via a rasterized preview), the parent
   *  passes the canonical URL here so the strip can offer a "↓ PDF"
   *  download chip. Three props together (href/label/name); leave
   *  href undefined to suppress the chip entirely. */
  downloadHref?: string
  downloadLabel?: string
  downloadName?: string
}


export default function RevisionStrip({ rev, onAction, hideChevrons, hideActions, downloadHref, downloadLabel, downloadName }: StripProps) {
  const [confirmSupersede, setConfirmSupersede] = useState(false)

  const e = rev.displayed
  if (!e) return null  // chain not loaded yet — don't flicker an empty bar
  const showChevrons = !hideChevrons && rev.total > 1
  const showActions = !hideActions

  // Number of revisions strictly NEWER than the displayed one. When the
  // user revises from a non-latest revision, these get marked superseded.
  const newerCount = rev.pos

  const onRevise = () => {
    if (!rev.isLatest) setConfirmSupersede(true)
    else onAction('revision', e)
  }
  const onConfirmSupersede = () => {
    setConfirmSupersede(false)
    onAction('revision-supersede', e)
  }

  // Keyboard nav (P2): ArrowLeft → older, ArrowRight → newer, when the
  // focus is on a chevron (or the strip itself). Scoped to chevrons so
  // arrow keys in surrounding text inputs still move the caret.
  const onChevKey = (ev: React.KeyboardEvent<HTMLButtonElement>) => {
    if (ev.key === 'ArrowLeft' && rev.canGoPrev) { ev.preventDefault(); rev.goPrev() }
    else if (ev.key === 'ArrowRight' && rev.canGoNext) { ev.preventDefault(); rev.goNext() }
  }

  return (
    <div className="rev-strip">
      {showChevrons && (
        <div className="rev-strip__nav" role="group" aria-label="Revision navigation">
          <button
            type="button"
            className="rev-strip__chev"
            disabled={!rev.canGoPrev}
            onClick={rev.goPrev}
            onKeyDown={onChevKey}
            title={rev.canGoPrev ? 'Older revision (← key)' : 'Already at oldest'}
            aria-label="Older revision"
          >‹</button>
          <span className="rev-strip__pos" title={`Revision ${rev.total - rev.pos} of ${rev.total}`}>
            rev <strong>{rev.total - rev.pos}</strong> / {rev.total}
            {rev.isLatest
              ? <span className="rev-strip__latest"> · latest</span>
              : <span className="rev-strip__not-latest"> · not latest</span>}
          </span>
          <button
            type="button"
            className="rev-strip__chev"
            disabled={!rev.canGoNext}
            onClick={rev.goNext}
            onKeyDown={onChevKey}
            title={rev.canGoNext ? 'Newer revision (→ key)' : 'Already at latest'}
            aria-label="Newer revision"
          >›</button>
        </div>
      )}
      {downloadHref && (
        <a
          className="rev-strip__download"
          href={downloadHref}
          download={downloadName ?? true}
          title={`Download the original ${downloadLabel ?? 'file'} (${downloadName ?? ''})`}
        >
          ↓ {downloadLabel ?? 'Download'}
        </a>
      )}
      {showActions && (
        <SplitButton
          primary={{
            label: '💬 Chat',
            title: `Bring "${e.title}" into chat`,
            onClick: () => onAction('chat', e),
          }}
          options={[
            {
              label: 'Chat about this',
              description: 'Bring it into the composer with the image attached',
              emphasis: true,
              onClick: () => onAction('chat', e),
            },
            {
              label: rev.isLatest ? 'Make a revision' : 'Make a revision…',
              description: rev.isLatest
                ? 'Re-run with a change; pinned as a sibling, becomes the new latest'
                : `Revise from rev ${rev.total - rev.pos}; supersedes the ${newerCount} newer revision${newerCount === 1 ? '' : 's'}`,
              onClick: onRevise,
            },
            {
              label: 'Reproduce',
              description: 'Re-run the producing code; flag any env drift',
              onClick: () => onAction('reproduce', e),
            },
          ]}
        />
      )}

      {confirmSupersede && (
        <ConfirmDialog
          title="Revise a non-latest revision?"
          variant="warning"
          primaryLabel="Revise (supersede newer)"
          onPrimary={onConfirmSupersede}
          onCancel={() => setConfirmSupersede(false)}
          body={
            <>
              <p>
                You're viewing <strong>rev {rev.total - rev.pos}</strong> of {rev.total}.
                {' '}There {newerCount === 1 ? 'is 1 newer revision' : `are ${newerCount} newer revisions`} after it.
              </p>
              <p>
                Revising from here will <strong>mark {newerCount === 1 ? 'that revision' : `those ${newerCount} revisions`} as superseded</strong>
                {' '}and your new revision will become the latest. The chain stays linear; superseded
                revisions remain in the database for audit.
              </p>
            </>
          }
        />
      )}
    </div>
  )
}
