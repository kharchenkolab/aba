/**
 * SessionPage — a session as a full page: the territory behind the map.
 *
 * The Record's strata are a curated redux; the real analysis lives in
 * sessions, runs, and results. This page is the session on its own terms:
 *  - the DISTILLATE up top (what entered the record from here) — the map
 *    side of the contract, so redux and source stay mutually checkable;
 *  - the full transcript with ADDRESSABLE TURNS (▷/▶ links from sediment
 *    lines, trail fragments, and search land here, scrolled + flashed);
 *  - the LEFTOVERS shelf — artifacts produced but never pinned, noted, or
 *    discussed. Curation missed them by definition; late review needs them
 *    findable ("now would be of potential interest — at least to check");
 *  - a live composer at the foot: filed ≠ dead — reading a session and
 *    continuing it are the same surface (chain edges record the lineage).
 *
 * The ⇥ control docks the same session into the right column (the capable
 * side-by-side working mode); ⤢ on the docked panel comes back here.
 */
import { useEffect, useState } from 'react'
import type { SessionRec, PanelMsg } from './world'
import { Msg } from './WorkPanel'

const ART = (id: string) => `/artifacts/coastal/${id}.svg`

const SCOPE_CLS: Record<string, string> = {
  project: 'proj', question: 'q', trail: 'trail', figure: 'fig', result: 'fig',
}

/** Number the human/agent exchanges; system rows (runs, figures, notes)
 *  attach to the turn above them. */
function numberTurns(msgs: PanelMsg[]): { m: PanelMsg; turn?: number }[] {
  let t = 0
  return msgs.map(m => (m.text && (m.role === 'you' || m.role === 'guide'))
    ? { m, turn: ++t }
    : { m })
}

export default function SessionPage({ sess, focusTurn, onBack, onDock }: {
  sess: SessionRec
  focusTurn?: number
  onBack: () => void
  onDock: () => void
}) {
  const [draft, setDraft] = useState('')
  const [extra, setExtra] = useState<PanelMsg[]>([])
  const send = () => {
    if (!draft.trim()) return
    setExtra(x => [...x, { role: 'you', text: draft.trim() },
      { role: 'guide', text: 'Canned in the storyboard — in the real system this REOPENS the session: it returns to the desk, and new runs land in the work record marked with this sitting.' }])
    setDraft('')
  }

  // ▷ links land on a specific exchange: scroll it into view and flash it
  useEffect(() => {
    if (!focusTurn) return
    const el = document.getElementById(`turn-${sess.id}-${focusTurn}`)
    if (!el) return
    const t = setTimeout(() => {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('flash')
      setTimeout(() => el.classList.remove('flash'), 1600)
    }, 60)
    return () => clearTimeout(t)
  }, [sess.id, focusTurn])

  const numbered = numberTurns(sess.msgs)
  return (
    <main className="doc doc--session">
      <div className="doc__viewbar">
        <button className="btn" onClick={onBack}>← back to the record</button>
        <button className="btn" onClick={onDock}
                title="dock this session into the right column — work side-by-side with the document">⇥ dock to the side</button>
        <span className="doc__viewnote">the redux is a map — this page is the territory: the whole exchange, every artifact, addressable by turn</span>
      </div>

      <header className="sp__head">
        <div className="sp__kicker">session · {sess.state}</div>
        <h1>{sess.label}</h1>
        <div className="sp__meta">
          {sess.when} · {sess.turns} turns · anchored to <b>{sess.anchor.label}</b>
        </div>
        <div className="wpanel__scope sp__scope">
          {sess.scope.map(s => (
            <span key={s.label} className={`wchip wchip--${SCOPE_CLS[s.kind] ?? 'proj'}`}>{s.label}</span>
          ))}
        </div>
        {sess.continues && (
          <div className="sp__chain" title="chain edge — this sitting picks up an earlier line of work">
            continues ← {sess.continues}
          </div>
        )}
        {sess.continuedBy && (
          <div className="sp__chain">continued by → {sess.continuedBy}</div>
        )}
      </header>

      {sess.distillate.length > 0 && (
        <section className="sp__dist">
          <div className="sp__rule">what entered the record from here</div>
          {sess.distillate.map((d, i) => (
            <div key={i} className="sp__distline">
              <span className="wdist__mark">✓</span>
              <span className="wdist__text">{d.text}</span>
              <span className="wdist__dest">→ {d.dest}</span>
            </div>
          ))}
        </section>
      )}

      {sess.leftovers.length > 0 && (
        <section className="sp__left">
          <div className="sp__rule sp__rule--left">
            leftovers — produced here, never pinned, noted, or discussed
          </div>
          <div className="sp__leftgrid">
            {sess.leftovers.map(o => (
              <figure key={o.id} className="sp__leftcard" title="unexamined — curation never touched it; that is exactly why it is kept findable">
                <img src={ART(o.id)} alt={o.title} />
                <figcaption>
                  {o.title}
                  {o.note && <span className="sp__leftnote">✦ {o.note}</span>}
                </figcaption>
              </figure>
            ))}
          </div>
          <div className="sp__leftfoot">
            the redux tells you what was kept — this shelf tells you what was passed over
          </div>
        </section>
      )}

      <section className="sp__transcript">
        <div className="sp__rule">transcript · turns are addressable — ▷ links land here</div>
        {numbered.map(({ m, turn }, i) => (
          <div key={i}
               className={`sp__turn ${turn ? 'sp__turn--n' : ''}`}
               id={turn ? `turn-${sess.id}-${turn}` : undefined}>
            {turn && <span className="sp__turnnum" title={`turn ${turn} — link target`}>{turn}</span>}
            <div className="sp__turnbody"><Msg m={m} /></div>
          </div>
        ))}
        {extra.map((m, i) => (
          <div key={`x${i}`} className="sp__turn"><span className="sp__turnnum" /><div className="sp__turnbody"><Msg m={m} /></div></div>
        ))}
      </section>

      <div className="sp__foot">
        <input value={draft}
               placeholder="Continue this line — reopening parks the session back on the desk…"
               onChange={e => setDraft(e.target.value)}
               onKeyDown={e => { if (e.key === 'Enter') send() }} />
        <button className="btn btn--primary" onClick={send}>↑</button>
      </div>
      <div className="sp__footnote">filed ≠ dead — reading a session and continuing it are the same surface</div>
    </main>
  )
}
