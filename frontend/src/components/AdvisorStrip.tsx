/**
 * Header advisor strip (ui2 M5) — replaces the right-hand advisor column.
 * Five subtle pills using our agent glyphs; quiet by default, tinted in the
 * agent's own colour with a count when it has notes. Clicking opens a light
 * popover (no full-canvas dim) with the note(s) + actions.
 */
import { useEffect, useRef, useState } from 'react'
import { AGENTS, AgentGlyph, agentColor, type AgentKey } from './icons'
import { typeOf } from '../entityTypes'
import './AdvisorStrip.css'

interface AdvisorNote {
  id: number
  entity_id: string
  advisor: string
  text: string
  created_at: string
  entity_type?: string | null
  entity_title?: string | null
}

interface Props {
  focusedId: string
  focusedType?: string
  onTry?: (text: string) => void
  onFocus?: (id: string) => void
}

function relativeTime(iso: string): string {
  const then = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').getTime()
  if (Number.isNaN(then)) return ''
  const s = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (s < 60) return 'just now'
  const m = Math.round(s / 60); if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60); if (h < 24) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}

export default function AdvisorStrip({ focusedId, focusedType, onTry, onFocus }: Props) {
  const [notes, setNotes] = useState<AdvisorNote[]>([])
  const [open, setOpen] = useState<AgentKey | null>(null)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false, stop = false
    async function load() {
      try {
        const r = await fetch(`/api/entities/${encodeURIComponent(focusedId)}/advisor-notes`)
        if (!r.ok) return
        const ns: AdvisorNote[] = await r.json()
        if (!cancelled) setNotes(ns)
      } catch { /* ignore */ }
    }
    setNotes([]); setOpen(null); load()
    let adviseTimer: ReturnType<typeof setTimeout> | undefined
    // Bio entity-types opt into auto-advise via advisors.on_focus_auto
    // in their YAML (today: dataset, narrative). The previous inline OR
    // check baked bio knowledge into a platform-shell component.
    if (typeOf(focusedType)?.advisors?.on_focus_auto === true) {
      adviseTimer = setTimeout(() => {
        fetch(`/api/entities/${encodeURIComponent(focusedId)}/advise`, { method: 'POST' })
          .then(() => { if (!stop) load() }).catch(() => {})
      }, 4000)
    }
    const tick = setInterval(() => { if (!stop) load() }, 10000)
    return () => { cancelled = true; stop = true; clearInterval(tick); if (adviseTimer) clearTimeout(adviseTimer) }
  }, [focusedId, focusedType])

  // Close the popover on outside click / Escape.
  useEffect(() => {
    function onDoc(e: MouseEvent) { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(null) }
    function onKey(e: globalThis.KeyboardEvent) { if (e.key === 'Escape') setOpen(null) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey) }
  }, [])

  function resolveNote(id: number, status: 'tried' | 'dismissed') {
    setNotes(prev => prev.filter(n => n.id !== id))
    fetch(`/api/advisor-notes/${id}/status`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    }).catch(() => {})
  }

  const byAdvisor: Record<string, AdvisorNote[]> = {}
  for (const n of notes) (byAdvisor[n.advisor] ??= []).push(n)
  const openNotes = open ? byAdvisor[open] ?? [] : []

  return (
    <div className="advisor-strip" ref={ref}>
      {AGENTS.map(a => {
        const adv = byAdvisor[a.key] ?? []
        const has = adv.length > 0
        return (
          <button
            key={a.key}
            className={`adv-pill ${has ? 'adv-pill--has' : 'adv-pill--quiet'} ${open === a.key ? 'adv-pill--open' : ''}`}
            style={has ? { color: agentColor(a.key) } : undefined}
            title={has ? `${a.name} · ${adv.length} idea${adv.length === 1 ? '' : 's'}` : `${a.name} · quiet`}
            onClick={() => setOpen(o => (o === a.key ? null : has ? a.key : null))}
          >
            <AgentGlyph agent={a.key} size={15} />
            {has && <span className="adv-pill__num">{adv.length}</span>}
          </button>
        )
      })}

      {open && openNotes.length > 0 && (
        <div className="adv-pop">
          <div className="adv-pop__arrow" />
          <div className="adv-pop__head" style={{ color: agentColor(open) }}>
            <AgentGlyph agent={open} size={16} />
            <span className="adv-pop__name">{AGENTS.find(a => a.key === open)?.name}</span>
            <span className="adv-pop__count">{openNotes.length} idea{openNotes.length === 1 ? '' : 's'}</span>
            <button className="adv-pop__x" onClick={() => setOpen(null)} title="Close">×</button>
          </div>
          <div className="adv-pop__body">
            {openNotes.map(n => (
              <div key={n.id} className="adv-pop__note">
                <div className="adv-pop__about">
                  {n.entity_title && (
                    <button className="adv-pop__subject" onClick={() => { onFocus?.(n.entity_id); setOpen(null) }}>
                      {n.entity_type ? `${n.entity_type} · ` : ''}{n.entity_title}
                    </button>
                  )}
                  <span className="adv-pop__time">{relativeTime(n.created_at)}</span>
                </div>
                <p className="adv-pop__text">{n.text}</p>
                <div className="adv-pop__actions">
                  {onTry && (
                    <button className="adv-pop__try" onClick={() => { onTry(n.text); resolveNote(n.id, 'tried') }}>Try it →</button>
                  )}
                  <button className="adv-pop__dismiss" onClick={() => resolveNote(n.id, 'dismissed')}>Dismiss</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
