/**
 * Claim view (ui3 P3/P4) — the rigor object. Statement + evidence + alternatives
 * + caveats with full user CRUD, the confidence ladder with a guarded,
 * reason-logged transition, and the status timeline. Compact in the peek; full
 * in entity-first.
 */
import { useState } from 'react'
import type { Entity } from '../types'
import EditableTitle from '../components/EditableTitle'
import './ClaimView.css'

const LADDER = ['preliminary', 'supported', 'validated'] as const
const SIDE = ['contested', 'refuted'] as const

interface Caveat { id: string; text: string; source?: string; dismissed?: boolean; rationale?: string }
interface Alt { id: string; text: string; source?: string; status?: string; rationale?: string; promoted_to?: string }
interface LogEntry { from: string | null; to: string; reason?: string; actor?: string; at?: string }
interface Note { id: string; text: string; source?: string; at?: string }

interface Props {
  claim: Entity
  entities: Entity[]
  onFocus: (id: string) => void
  onChange: () => void
  compact?: boolean
}

export default function ClaimView({ claim, entities, onFocus, onChange, compact }: Props) {
  const m = (claim.metadata ?? {}) as Record<string, unknown>
  const confidence = (m.confidence as string) ?? 'preliminary'
  const evidenceIds = (m.evidence_ids as string[]) ?? []
  const caveats = (m.caveats as Caveat[]) ?? []
  const alternatives = (m.alternatives as Alt[]) ?? []
  const notes = (m.notes as Note[]) ?? []
  const log = (m.status_log as LogEntry[]) ?? []
  const statement = (m.statement as string) ?? claim.title

  const api = `/api/claims/${encodeURIComponent(claim.id)}`
  const call = async (method: string, path: string, body?: unknown) => {
    await fetch(`${api}${path}`, {
      method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    onChange()
  }

  const [pending, setPending] = useState<string | null>(null)   // status target awaiting a reason
  const [reason, setReason] = useState('')
  const [newCaveat, setNewCaveat] = useState('')
  const [newAlt, setNewAlt] = useState('')
  const [newNote, setNewNote] = useState('')
  const [addEv, setAddEv] = useState(false)

  const evidence = evidenceIds.map(id => entities.find(e => e.id === id)).filter(Boolean) as Entity[]
  const candidates = entities.filter(e =>
    (e.type === 'figure' || e.type === 'table') && e.status !== 'archived' && !evidenceIds.includes(e.id))

  if (compact) {
    return (
      <div className="claim claim--compact">
        <StatusPill confidence={confidence} />
        <p className="claim__statement">{statement}</p>
        <div className="claim__compact-meta">
          {evidence.length} evidence · {caveats.filter(c => !c.dismissed).length} caveats · {alternatives.filter(a => a.status === 'open').length} alternatives
        </div>
      </div>
    )
  }

  async function transition(to: string) {
    if (to === confidence) return
    setPending(to); setReason('')
  }
  async function confirmTransition() {
    if (!pending) return
    await call('POST', '/status', { to: pending, reason: reason.trim() })
    setPending(null); setReason('')
  }

  return (
    <div className="claim">
      {/* statement — a claim has no separate title, so the statement IS the
          editable headline. Uses the shared EditableTitle (same hover + in-place
          edit as every card title). */}
      <EditableTitle as="p" multiline className="claim__statement" value={statement} ariaLabel="Edit statement"
        onCommit={t => call('PATCH', '', { statement: t })} />

      {/* status ladder */}
      <div className="claim__status">
        {LADDER.map(s => (
          <button key={s} className={`claim__pill ${confidence === s ? 'is-on' : ''} claim__pill--${s}`}
            onClick={() => transition(s)}>{s}</button>
        ))}
        <span className="claim__status-sep">/</span>
        {SIDE.map(s => (
          <button key={s} className={`claim__pill ${confidence === s ? 'is-on' : ''} claim__pill--${s}`}
            onClick={() => transition(s)}>{s}</button>
        ))}
      </div>
      {pending && (
        <div className="claim__transition">
          <span>→ <b>{pending}</b>{pending === 'validated' ? ' (robustness note required)' : ''}:</span>
          <input autoFocus value={reason} onChange={e => setReason(e.target.value)} placeholder="reason for this change…"
            onKeyDown={e => { if (e.key === 'Enter' && (pending !== 'validated' || reason.trim())) confirmTransition(); if (e.key === 'Escape') setPending(null) }} />
          <button className="claim__btn" disabled={pending === 'validated' && !reason.trim()} onClick={confirmTransition}>confirm</button>
          <button className="claim__btn" onClick={() => setPending(null)}>cancel</button>
        </div>
      )}

      {/* evidence */}
      <Section label="Evidence">
        {evidence.length === 0 && <div className="claim__empty">No evidence yet.</div>}
        {evidence.map(ev => (
          <div key={ev.id} className="claim__ev">
            <span className="claim__ev-title" onClick={() => onFocus(ev.id)}>{ev.title}</span>
            <button className="claim__x" title="Remove evidence" onClick={() => call('DELETE', `/evidence/${ev.id}`)}>×</button>
          </div>
        ))}
        {addEv ? (
          <div className="claim__add-ev">
            {candidates.length === 0 && <div className="claim__empty">No other results to add.</div>}
            {candidates.slice(0, 12).map(c => (
              <button key={c.id} className="claim__add-ev-item"
                onClick={() => { call('POST', '/evidence', { result_id: c.id }); setAddEv(false) }}>+ {c.title}</button>
            ))}
            <button className="claim__btn" onClick={() => setAddEv(false)}>close</button>
          </div>
        ) : (
          <button className="claim__add" onClick={() => setAddEv(true)}>+ add evidence</button>
        )}
      </Section>

      {/* alternatives */}
      <Section label="Alternatives">
        {alternatives.map(a => (
          <div key={a.id} className={`claim__item is-${a.status}`}>
            <span className="claim__item-text">{a.text}{a.source && a.source !== 'user' && <em className="claim__src"> · {a.source}</em>}{a.rationale && <span className="claim__rationale"> — dismissed: {a.rationale}</span>}</span>
            <span className="claim__item-actions">
              {a.status === 'open' && <button title="Promote to its own claim" onClick={() => call('POST', `/alternatives/${a.id}/promote`)}>➦</button>}
              {a.status === 'open' && <button title="Dismiss with rationale" onClick={() => { const r = prompt('Why dismiss this alternative?') ?? ''; call('PATCH', `/alternatives/${a.id}`, { status: 'dismissed', rationale: r }) }}>⊘</button>}
              <button title="Remove" onClick={() => call('DELETE', `/alternatives/${a.id}`)}>×</button>
            </span>
          </div>
        ))}
        <input className="claim__input" value={newAlt} onChange={e => setNewAlt(e.target.value)} placeholder="+ a competing explanation"
          onKeyDown={e => { if (e.key === 'Enter' && newAlt.trim()) { call('POST', '/alternatives', { text: newAlt.trim() }); setNewAlt('') } }} />
      </Section>

      {/* caveats */}
      <Section label="Caveats">
        {caveats.map(c => (
          <div key={c.id} className={`claim__item ${c.dismissed ? 'is-dismissed' : ''}`}>
            <span className="claim__item-text">{c.text}{c.source && c.source !== 'user' && <em className="claim__src"> · {c.source}</em>}{c.dismissed && c.rationale && <span className="claim__rationale"> — dismissed: {c.rationale}</span>}</span>
            <span className="claim__item-actions">
              {!c.dismissed && <button title="Dismiss with rationale" onClick={() => { const r = prompt('Why dismiss this caveat?') ?? ''; call('PATCH', `/caveats/${c.id}`, { dismissed: true, rationale: r }) }}>⊘</button>}
              {c.dismissed && <button title="Reinstate" onClick={() => call('PATCH', `/caveats/${c.id}`, { dismissed: false })}>↺</button>}
              <button title="Remove" onClick={() => call('DELETE', `/caveats/${c.id}`)}>×</button>
            </span>
          </div>
        ))}
        <input className="claim__input" value={newCaveat} onChange={e => setNewCaveat(e.target.value)} placeholder="+ a caveat"
          onKeyDown={e => { if (e.key === 'Enter' && newCaveat.trim()) { call('POST', '/caveats', { text: newCaveat.trim() }); setNewCaveat('') } }} />
      </Section>

      {/* notes — free-form thoughts the user (or agent) wants to keep with the claim */}
      <Section label="Notes">
        {notes.map(n => (
          <div key={n.id} className="claim__item">
            <span className="claim__item-text">{n.text}{n.source && n.source !== 'user' && <em className="claim__src"> · {n.source}</em>}</span>
            <span className="claim__item-actions">
              <button title="Remove" onClick={() => call('DELETE', `/notes/${n.id}`)}>×</button>
            </span>
          </div>
        ))}
        <input className="claim__input" value={newNote} onChange={e => setNewNote(e.target.value)} placeholder="+ add a note"
          onKeyDown={e => { if (e.key === 'Enter' && newNote.trim()) { call('POST', '/notes', { text: newNote.trim() }); setNewNote('') } }} />
      </Section>

      {/* status timeline */}
      <Section label="Status history">
        {log.map((l, i) => (
          <div key={i} className="claim__log">
            <span className="claim__log-to">{l.from ? `${l.from} → ` : ''}{l.to}</span>
            {l.reason && <span className="claim__log-reason">{l.reason}</span>}
            <span className="claim__log-meta">{l.actor}{l.at ? ` · ${new Date(l.at).toLocaleDateString()}` : ''}</span>
          </div>
        ))}
      </Section>
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="claim__section">
      <div className="claim__section-label">{label}</div>
      {children}
    </div>
  )
}

function StatusPill({ confidence }: { confidence: string }) {
  return <span className={`claim__pill is-on claim__pill--${confidence}`}>{confidence}</span>
}
