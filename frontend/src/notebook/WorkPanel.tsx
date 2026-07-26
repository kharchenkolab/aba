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
  const [flagged, setFlagged] = useState(false)
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
        {[...panel.msgs, ...extra].map((m, i) => (
          <div key={i}>
            <Msg m={m} />
            {m.ref && (
              <button className="wpanel__showref" onClick={() => onShowRef?.(m.ref!.el)}
                      title="locate this on the page — highlighted where it stands">
                {m.ref.label}
              </button>
            )}
          </div>
        ))}
        {panel.crossFlag && (
          <div className="wpanel__cross" title="cross-boundary relevance stays a proposal — the agent never writes outside the anchor silently">
            ✦ {panel.crossFlag.text}
            {flagged
              ? <span className="wpanel__crossdone">✓ noted → Q2 (draft)</span>
              : <button className="btn" onClick={() => setFlagged(true)}>{panel.crossFlag.accept}</button>}
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
