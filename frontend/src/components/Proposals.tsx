/**
 * Proactive proposals (Phase D). The Guide/advisors notice something and propose
 * an action; each is attributed, dismissible, and reversible. This module owns
 * the polling hook + the in-context card UI + an undo toast.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { AgentGlyph, agentColor, type AgentKey } from './icons'
import './Proposals.css'

export interface Proposal {
  id: number
  thread_id: string | null
  kind: string
  advisor: string
  headline: string
  body: string | null
  payload: Record<string, unknown> | null
  status: string
}

const ACCEPT_LABEL: Record<string, string> = {
  convergence: 'Draft claim',
  question: 'Use it',
  title: 'Rename',
  subquestion: 'File it',
  return_wrap: 'Got it',
  nplus1: 'Re-check',
}
const ACCEPTED_LABEL: Record<string, string> = {
  convergence: 'Claim drafted',
  question: 'Question updated',
  title: 'Thread renamed',
  subquestion: 'Open question filed',
  return_wrap: 'Acknowledged',
  nplus1: 'Noted',
}

export function useProposals(threadId: string | null, onWorldChange?: () => void) {
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [undoable, setUndoable] = useState<{ id: number; label: string } | null>(null)
  const undoTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const load = useCallback(async () => {
    if (!threadId) { setProposals([]); return }
    try {
      const r = await fetch(`/api/threads/${encodeURIComponent(threadId)}/proposals?status=pending`)
      if (r.ok) setProposals(await r.json())
    } catch { /* ignore */ }
  }, [threadId])

  useEffect(() => {
    load()
    const tick = setInterval(load, 4000)
    return () => clearInterval(tick)
  }, [load])

  const accept = useCallback(async (p: Proposal) => {
    setProposals(prev => prev.filter(x => x.id !== p.id))   // optimistic
    const ok = await fetch(`/api/proposals/${p.id}/accept`, { method: 'POST' })
      .then(r => r.ok).catch(() => false)
    onWorldChange?.()
    if (ok) {   // no success toast + Undo for an accept that never happened
      setUndoable({ id: p.id, label: ACCEPTED_LABEL[p.kind] ?? 'Done' })
      clearTimeout(undoTimer.current)
      undoTimer.current = setTimeout(() => setUndoable(null), 8000)
    }
    load()      // reconciles: a failed accept resurrects the card
  }, [load, onWorldChange])

  const dismiss = useCallback(async (p: Proposal) => {
    setProposals(prev => prev.filter(x => x.id !== p.id))
    await fetch(`/api/proposals/${p.id}/dismiss`, { method: 'POST' }).catch(() => {})
    load()
  }, [load])

  const undo = useCallback(async (id: number) => {
    setUndoable(null)
    await fetch(`/api/proposals/${id}/undo`, { method: 'POST' }).catch(() => {})
    onWorldChange?.()
    load()
  }, [load, onWorldChange])

  return { proposals, undoable, accept, dismiss, undo, clearUndo: () => setUndoable(null) }
}

export function ProposalCard({ p, onAccept, onDismiss }: {
  p: Proposal
  onAccept: (p: Proposal) => void
  onDismiss: (p: Proposal) => void
}) {
  const adv = p.advisor as AgentKey
  return (
    <div className="proposal" style={{ borderLeftColor: agentColor(adv) }}>
      <div className="proposal__head">
        <span className="proposal__mark" style={{ color: agentColor(adv) }}>
          <AgentGlyph agent={adv} size={13} />
        </span>
        <span className="proposal__headline">{p.headline}</span>
        <button className="proposal__x" title="Dismiss" onClick={() => onDismiss(p)}>×</button>
      </div>
      {p.body && <p className="proposal__body">{p.body}</p>}
      <div className="proposal__actions">
        <button className="proposal__accept" onClick={() => onAccept(p)}>
          {ACCEPT_LABEL[p.kind] ?? 'Accept'}
        </button>
        <button className="proposal__dismiss" onClick={() => onDismiss(p)}>Dismiss</button>
      </div>
    </div>
  )
}

export function UndoToast({ undoable, onUndo, onClose }: {
  undoable: { id: number; label: string } | null
  onUndo: (id: number) => void
  onClose: () => void
}) {
  if (!undoable) return null
  return (
    <div className="proposal-toast">
      <span className="proposal-toast__check">✓</span>
      <span>{undoable.label}</span>
      <button className="proposal-toast__undo" onClick={() => onUndo(undoable.id)}>Undo</button>
      <button className="proposal-toast__x" onClick={onClose} title="Close">×</button>
    </div>
  )
}
