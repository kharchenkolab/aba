/**
 * WorkPanel — a working session, open OVER the document.
 *
 * The same instrument as the margin bench at a wider scope: the panel is
 * summoned from somewhere (project / question / trail / figure) and the
 * agent starts with that scope already in hand — zero context-setting.
 * Runs launched here land in the sediment at launch (the document records
 * the action, not the intention); results return INTO the conversation;
 * what deserves keeping is drafted into the strata while you work.
 *
 * Session close is a distillation moment: the panel proposes what enters
 * the record (fragments, addenda, keeps); the transcript files under its
 * anchor — reachable from the section (▷) and from every sediment line it
 * produced, never rendered in the document body.
 *
 * Rendered read-only for archived transcripts (the ▷ links).
 */
import { useEffect, useRef, useState } from 'react'
import type { PanelState, PanelMsg } from './world'

const ART = (id: string) => `/artifacts/coastal/${id}.svg`

const SCOPE_CLS: Record<string, string> = {
  project: 'proj', question: 'q', trail: 'trail', figure: 'fig', result: 'fig',
}

export function Msg({ m }: { m: PanelMsg }) {
  if (m.note) return <div className="wpanel__sysnote">✦ {m.note}</div>
  if (m.run) {
    return (
      <div className={`wrun wrun--${m.run.state}`}
           title={m.run.state === 'running'
             ? 'launched from this exchange — already a line in the sediment'
             : 'finished — its sediment line carries the verdict and retention'}>
        <span className="wrun__state">{m.run.state === 'running' ? '▶' : '·'}</span>
        <span className="wrun__title">{m.run.title}</span>
        <span className="wrun__meta">{m.run.meta}</span>
      </div>
    )
  }
  if (m.fig) {
    return (
      <figure className="wfig">
        <img src={ART(m.fig.id)} alt={m.fig.stat} />
        <figcaption>{m.fig.stat}</figcaption>
      </figure>
    )
  }
  return (
    <div className={`bmsg bmsg--${m.role}`}>
      <span className="bmsg__who">{m.role === 'you' ? 'you' : 'Guide'}</span>
      <p>{m.text}</p>
    </div>
  )
}

export default function WorkPanel({ panel, onClose, onAdvance, onExpand, continuable, lookingAt, onShowRef }: {
  panel: PanelState
  onClose?: () => void
  onAdvance?: (t: string) => void
  /** docked transcripts can expand into the session's full page */
  onExpand?: () => void
  /** a filed session is not dead — reading it and continuing it are the same surface */
  continuable?: boolean
  /** deixis, doc → chat: the current subject, driven by clicks on the document */
  lookingAt?: string
  /** deixis, chat → doc: message refs locate their element on the page */
  onShowRef?: (elId: string) => void
}) {
  const [extra, setExtra] = useState<PanelMsg[]>([])
  const [draft, setDraft] = useState('')
  const [flagged, setFlagged] = useState<false | 'note' | 'plan'>(false)
  // chat gestures: one-tap verbs on hover — the user's ground truth about
  // salience and placement, injected at the moment of noticing; each leaves
  // a RECEIPT (the pre-filled routing-table row), undoable
  const [gest, setGest] = useState<Record<number, string>>({})
  const gesture = (i: number, receipt: string) => setGest(g => ({ ...g, [i]: receipt }))
  const ungesture = (i: number) => setGest(g => { const n = { ...g }; delete n[i]; return n })
  // like any chat: open at the latest exchange (and keep up as it grows).
  // Re-pin after a beat — figure images load async and grow the scroll
  // height after the first pass.
  const msgsRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = msgsRef.current
    if (!el) return
    const toBottom = () => { el.scrollTop = el.scrollHeight }
    toBottom()
    const t1 = setTimeout(toBottom, 150)
    const t2 = setTimeout(toBottom, 600)
    return () => { clearTimeout(t1); clearTimeout(t2) }
  }, [panel, extra])
  const send = () => {
    if (!draft.trim()) return
    setExtra(x => [...x, { role: 'you', text: draft.trim() },
      { role: 'guide', text: 'Canned in the storyboard — in the real system this is the live agent, and the run you just asked for would already be a ▶ line in the sediment.' }])
    setDraft('')
  }
  const archived = !!panel.archived
  return (
    <aside className={`wpanel ${archived ? 'wpanel--archived' : ''}`}>
      <div className="wpanel__head">
        <div className="wpanel__headrow">
          <div className="wpanel__kicker">
            {archived
              ? `archived transcript · ${panel.archived!.label} · ${panel.archived!.when}`
              : 'working session'}
          </div>
          {panel.status && !archived && <div className="wpanel__status">{panel.status}</div>}
          {!archived && !panel.closing && (
            <button className="wpanel__distill"
                    title="checkpoint distillation — the routing table, mid-stream: review and ratify what this sitting has drafted so far without closing anything. Close is the floor, not the gate: it only guarantees this review happens at least once per episode"
                    onClick={() => setExtra(x => [...x, { role: 'system', note: 'checkpoint — everything drafted so far reviewed in place; nothing closed, the sitting continues' }])}>
              distill so far
            </button>
          )}
          {onExpand && (
            <button className="wpanel__close" onClick={onExpand}
                    title="open as a full page — for sifting artifacts, reading the whole exchange">⤢</button>
          )}
          {onClose && <button className="wpanel__close" onClick={onClose}>✕</button>}
        </div>
        <div className="wpanel__scope" title="what the agent already has in hand — decided by WHERE you opened the session, not by re-explaining">
          {panel.scope.map(s => (
            <span key={s.label} className={`wchip wchip--${SCOPE_CLS[s.kind] ?? 'proj'}`}>{s.label}</span>
          ))}
        </div>
        {panel.scopeNote && <div className="wpanel__scopenote">{panel.scopeNote}</div>}
        {panel.touched && panel.touched.length > 0 && (
          <div className="wpanel__touched" title="the impact set — where this session has landed things; at close, this is exactly what the distillation reviews">
            touched: {panel.touched.map(t => <span key={t} className="wpanel__touchchip">{t}</span>)}
          </div>
        )}
        {(lookingAt ?? panel.lookingAt) && (
          <div className="wpanel__look" title="the conversation's current subject — click any element on the page to point at it; no context-setting needed">
            looking at: <b>{lookingAt ?? panel.lookingAt}</b>
          </div>
        )}
      </div>

      <div className="wpanel__msgs" ref={msgsRef}>
        {/* single inner child + column-reverse outer = natively bottom-anchored
            scroll (chat behavior), immune to async image-load growth */}
        <div className="wpanel__msgsinner">
        {[...panel.msgs, ...extra].map((m, i) => {
          const gesturable = !archived && !panel.closing && ((m.role === 'guide' && m.text) || m.fig)
          return (
          <div key={i} className="wmsg">
            <Msg m={m} />
            {m.ref && (
              <button className="wpanel__showref" onClick={() => onShowRef?.(m.ref!.el)}
                      title="locate this on the page — highlighted where it stands">
                {m.ref.label}
              </button>
            )}
            {gesturable && !gest[i] && (
              <div className="gest">
                {m.text && (
                  <>
                    <button title="this matters — unaimed salience: the agent must route it (a marked element can never fade), destination its problem; one tap teaches the salience model"
                            onClick={() => gesture(i, 'marked — the agent must route this; it cannot fade')}>mark</button>
                    <button title="the pin you already know (keep_message) — births a note, filed directly (your own noticing needs no ratification), pointed at its element, turn-stamped. Hold to aim: → trail · → question"
                            onClick={() => gesture(i, 'pinned → note · turn-stamped — the agent will propose its trail')}>pin</button>
                    <button title="later, aimed — lands as a planned item under this question (no ceremony; it is your plan)"
                            onClick={() => gesture(i, '→ planned under this question')}>→ plan</button>
                    <button title="formalize this — the record-writer drafts a claim from this statement; the draft goes to the inbox (this one IS a decision — the ratify gate holds)"
                            onClick={() => gesture(i, 'claim draft requested → inbox')}>draft claim</button>
                    <button title="the anti-gesture: don't build on this. Wears a dissent mark; the agent may not cite it without surfacing the dispute; remembered until the world changes"
                            onClick={() => gesture(i, '✗ disputed — will not be cited without the dispute')}>✗</button>
                  </>
                )}
                {m.fig && (
                  <>
                    <button title="retention, decided at the moment of seeing — kept durably, not left to the sweep"
                            onClick={() => gesture(i, 'kept ✓ durably')}>keep ✓</button>
                    <button title="the pin you already know (keep_message) — births a note pointed at this figure, turn-stamped"
                            onClick={() => gesture(i, 'pinned → note · pointed at this figure')}>pin</button>
                    <button title="park it on the desk for two-locus work; clears at session close"
                            onClick={() => gesture(i, '⌖ held on the desk')}>⌖ hold</button>
                  </>
                )}
              </div>
            )}
            {gest[i] && (
              <div className="gest__done">
                ✓ {gest[i]}
                <button onClick={() => ungesture(i)}>undo</button>
              </div>
            )}
          </div>
          )
        })}
        {panel.crossFlag && (
          <div className="wpanel__cross" title="cross-boundary relevance stays a proposal — the agent never writes outside the anchor silently">
            ✦ {panel.crossFlag.text}
            {flagged === 'note' && <span className="wpanel__crossdone">✓ noted → Q2 (draft)</span>}
            {flagged === 'plan' && <span className="wpanel__crossdone">✓ planned under Q2 — waits there with its own ▷ work</span>}
            {!flagged && (
              <>
                <button className="btn" onClick={() => setFlagged('note')}>{panel.crossFlag.accept}</button>
                <button className="btn" onClick={() => setFlagged('plan')}
                        title="later is a place, not a pile — lands as a planned item under Q2, no ceremony; it is your plan">
                  plan it for later
                </button>
              </>
            )}
          </div>
        )}

        {panel.closing && (
          <div className="wclose">
            <div className="wclose__rule">session close — the distillation moment</div>
            <div className="wclose__sum">{panel.closing.summary}</div>
            {panel.closing.distillates.map((d, i) => (
              <div key={i} className={`wdist wdist--${d.state === 'accepted' ? 'ok' : 'pending'}`}>
                <span className="wdist__mark">{d.state === 'accepted' ? '✓' : '▢'}</span>
                <span className="wdist__text">{d.text}</span>
                <span className="wdist__dest">→ {d.dest}</span>
                <em className="wdist__state">{d.state}</em>
              </div>
            ))}
            <div className="wclose__actions">
              <button className="btn btn--primary" onClick={() => onAdvance?.('file-close')}>file &amp; close</button>
              <button className="btn" title="the session stays open on the desk — resumable, nothing lost">leave open</button>
            </div>
            <div className="wclose__note">
              what enters the record is exactly what you ratify — the transcript files
              under its question, out of the way but one click from everything it touched.
              or just walk away: sittings end by attention — an idle session parks itself,
              its routing table waits in the tray, and nothing is ever lost to a missing goodbye
            </div>
          </div>
        )}
        </div>
      </div>

      {(continuable || (!archived && !panel.closing)) && (
        <div className="wpanel__foot">
          <input value={draft}
                 placeholder={archived ? 'Continue this line — reopening parks it back on the desk…'
                                       : 'Ask, or ask for work — runs launch from here…'}
                 onChange={e => setDraft(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') send() }} />
          <button className="btn btn--primary" onClick={send}>↑</button>
        </div>
      )}
      <div className="wpanel__note">
        {archived
          ? 'filed, not dead — its products are in the strata; continuing reopens the line'
          : 'runs land in the sediment as they launch · the transcript files under its anchor at close'}
      </div>
    </aside>
  )
}
