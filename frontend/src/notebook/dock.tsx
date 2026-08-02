/** The work dock — §5/§6's docked instrument over the live substrate.
 *
 *  ONE right panel, summonable from every noun on the page. The anchor
 *  names what summoned it (question, plan item, claim, figure, thread);
 *  the header renders the anchor's kind (dossier for a claim, image +
 *  provenance for a figure); below it the line's REAL transcript
 *  (rendered markdown, run outputs stitched into the working-step gaps);
 *  at the foot, always, the composer — and the composer RUNS: it posts
 *  to the same /api/chat the workspace uses, the turn streams on the
 *  server, the dock polls it home (cancel and awaiting-you surfaced).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { World } from './world'

export interface DockAnchor {
  /** what summoned the dock — decides the header pane */
  kind?: 'question' | 'plan' | 'claim' | 'figure' | 'thread'
  threadId: string
  title: string
  /** claim/figure entity behind the anchor — rides /api/chat as the
   *  turn's focus_entity_id, so the guide starts LOOKING AT it */
  entityId?: string
  /** composer prefill (a plan item's text) — reviewed, never auto-sent */
  seed?: string
  /** transcript scoping for frozen sittings */
  from?: string
  to?: string
  /** fires when a plan anchor's seeded ask is actually SENT — the item
   *  flips to taken-up at launch, not at click */
  onLaunched?: () => void
}

// ------------------------------------------------------------ tiny markdown
// The transcript is agent prose: headings, bold, inline code, lists. A
// 40-line renderer beats shipping a dependency for four constructs.

function mdInline(s: string, key = 0): ReactNode[] {
  const out: ReactNode[] = []
  let rest = s
  let k = key
  const re = /(\*\*([^*]+)\*\*|`([^`]+)`)/
  while (rest) {
    const m = re.exec(rest)
    if (!m) { out.push(rest); break }
    if (m.index > 0) out.push(rest.slice(0, m.index))
    if (m[2] !== undefined) out.push(<b key={`b${k++}`}>{m[2]}</b>)
    else out.push(<code key={`c${k++}`}>{m[3]}</code>)
    rest = rest.slice(m.index + m[0].length)
  }
  return out
}

export function mdBlocks(text: string): ReactNode[] {
  const out: ReactNode[] = []
  const lines = text.split('\n')
  let para: string[] = []
  let list: string[] = []
  let k = 0
  const flushPara = () => {
    if (para.length) { out.push(<p key={`p${k++}`}>{mdInline(para.join(' '))}</p>); para = [] }
  }
  const flushList = () => {
    if (list.length) {
      out.push(<ul key={`u${k++}`}>{list.map((li, i) => <li key={i}>{mdInline(li)}</li>)}</ul>)
      list = []
    }
  }
  for (const raw of lines) {
    const line = raw.trimEnd()
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    const li = /^\s*[-*]\s+(.*)$/.exec(line)
    if (h) { flushPara(); flushList(); out.push(<div className="dk__h" key={`h${k++}`}>{mdInline(h[2])}</div>) }
    else if (li) { flushPara(); list.push(li[1]) }
    else if (!line.trim()) { flushPara(); flushList() }
    else { flushList(); para.push(line) }
  }
  flushPara(); flushList()
  return out
}

// --------------------------------------------------------------- transcript

interface Msg { role: string; text: string; ts: string; steps?: number }

function useTranscript(w: World, a: DockAnchor, refreshKey: number) {
  const [msgs, setMsgs] = useState<Msg[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    const q = new URLSearchParams()
    if (w.projectId) q.set('project_id', w.projectId)
    q.set('thread_id', a.threadId)
    fetch(`${w.apiBase ?? ''}/api/entities/workspace/messages?${q}`)
      .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json() })
      .then((rows: { role: string; content: unknown; ts?: string }[]) => {
        const out: Msg[] = []
        let steps = 0
        for (const m of rows) {
          const ts = m.ts || ''
          if (a.from && ts && ts < a.from) continue
          if (a.to && ts && ts > a.to) continue
          const text = typeof m.content === 'string' ? m.content
            : Array.isArray(m.content)
              ? m.content.filter((b): b is { type: string; text: string } =>
                  !!b && (b as { type?: string }).type === 'text')
                  .map(b => b.text).join('\n').trim()
              : ''
          if (!text) { steps++; continue }
          if (steps > 0 && out.length) out[out.length - 1].steps = (out[out.length - 1].steps ?? 0) + steps
          steps = 0
          out.push({ role: m.role, text, ts })
        }
        setErr(null)
        setMsgs(out)
      })
      .catch(e => setErr(String(e)))
  }, [w.apiBase, w.projectId, a.threadId, a.from, a.to, refreshKey])
  return { msgs, err }
}

/** outputs produced on this thread between two message timestamps — the
 *  "· N working steps ·" divider opens onto what those steps LEFT */
function outputsBetween(w: World, threadId: string, t0: string, t1: string) {
  return w.sediment
    .filter(e => e.threadRef === threadId && e.ts && e.ts >= t0 && e.ts <= t1)
    .flatMap(e => e.shown.filter(o => o.artifact))
}

// ------------------------------------------------------------- the composer

type TurnState =
  | { phase: 'idle' }
  | { phase: 'working'; runId?: string }
  | { phase: 'awaiting'; runId: string; question?: string }

function useActiveTurn(w: World, threadId: string) {
  const get = useCallback(async (): Promise<{ run_id: string; state: string } | null> => {
    const q = w.projectId ? `?project_id=${encodeURIComponent(w.projectId)}` : ''
    try {
      const r = await fetch(`${w.apiBase ?? ''}/api/threads/${threadId}/active-turn${q}`)
      if (!r.ok) return null
      return await r.json()
    } catch { return null }
  }, [w.apiBase, w.projectId, threadId])
  return get
}

// ------------------------------------------------------------------ the dock

export default function WorkDock({ w, anchor, onClose, onWorldStale }: {
  w: World
  anchor: DockAnchor
  onClose: () => void
  /** a turn finished — the page's world is stale; the host refetches */
  onWorldStale?: () => void
}) {
  const [refreshKey, setRefreshKey] = useState(0)
  const { msgs, err } = useTranscript(w, anchor, refreshKey)
  const [draft, setDraft] = useState(anchor.seed ?? '')
  const [turn, setTurn] = useState<TurnState>({ phase: 'idle' })
  const [sendErr, setSendErr] = useState<string | null>(null)
  const activeTurn = useActiveTurn(w, anchor.threadId)
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const pid = w.projectId

  // anchor change: new seed, fresh transcript scroll
  useEffect(() => { setDraft(anchor.seed ?? '') },
    [anchor.threadId, anchor.entityId, anchor.seed])

  // a turn may ALREADY be live on this line (opened mid-work) — surface it
  useEffect(() => {
    let on = true
    activeTurn().then(t => {
      if (!on || !t) return
      if (t.state === 'awaiting_user') setTurn({ phase: 'awaiting', runId: t.run_id })
      else if (t.state === 'running') setTurn({ phase: 'working', runId: t.run_id })
    })
    return () => { on = false }
  }, [activeTurn])

  // while working: poll — messages accrete into the transcript as they
  // land; when the turn ends the world above the dock is stale
  useEffect(() => {
    if (turn.phase !== 'working') return
    const iv = setInterval(async () => {
      setRefreshKey(k => k + 1)
      const t = await activeTurn()
      if (!t) {
        setTurn({ phase: 'idle' })
        setRefreshKey(k => k + 1)
        onWorldStale?.()
      } else if (t.state === 'awaiting_user') {
        setTurn({ phase: 'awaiting', runId: t.run_id })
      }
    }, 2500)
    return () => clearInterval(iv)
  }, [turn.phase, activeTurn, onWorldStale])

  // auto-follow the tail while a turn streams in
  useEffect(() => {
    if (turn.phase === 'working' && bodyRef.current)
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [msgs, turn.phase])

  const send = async () => {
    const text = draft.trim()
    if (!text || turn.phase === 'working') return
    setSendErr(null)
    setDraft('')
    try {
      if (turn.phase === 'awaiting') {
        // the guide asked YOU — this reply resumes the held turn
        const r = await fetch(`${w.apiBase ?? ''}/api/turns/${turn.runId}/resume`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_text: text, ...(pid ? { project_id: pid } : {}) }),
        })
        if (!r.ok) throw new Error(`resume ${r.status}`)
        setTurn({ phase: 'working', runId: turn.runId })
        return
      }
      const body: Record<string, unknown> = { text, thread_id: anchor.threadId }
      if (pid) body.project_id = pid
      if (anchor.entityId) body.focus_entity_id = anchor.entityId
      // fire the turn; the response is an SSE stream we deliberately do
      // not hold open — the poll loop reads the same state back
      const r = await fetch(`${w.apiBase ?? ''}/api/chat`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) throw new Error(`chat ${r.status}`)
      r.body?.cancel()
      if (anchor.kind === 'plan') anchor.onLaunched?.()
      setTurn({ phase: 'working' })
      setRefreshKey(k => k + 1)
    } catch (e) {
      setSendErr(String(e))
      setDraft(text)
      setTurn({ phase: 'idle' })
    }
  }

  const cancel = async () => {
    const t = await activeTurn()
    if (t) {
      await fetch(`${w.apiBase ?? ''}/api/turns/${t.run_id}/cancel`, { method: 'POST' })
        .catch(() => {})
    }
    setTurn({ phase: 'idle' })
    setRefreshKey(k => k + 1)
  }

  const claim = anchor.kind === 'claim' && anchor.entityId
    ? w.claims[anchor.entityId] : undefined
  const kindWord = anchor.kind === 'question' ? 'line of inquiry'
    : anchor.kind === 'plan' ? 'planned analysis — review the ask, then send'
    : anchor.kind === 'claim' ? 'claim'
    : anchor.kind === 'figure' ? 'figure'
    : 'line of inquiry'

  return (
    <aside className="dk" aria-label="work dock">
      <div className="dk__head">
        <div className="dk__kicker">{kindWord}</div>
        <div className="dk__title">{anchor.title}</div>
        <button className="dk__close" onClick={onClose} title="close (Esc)">✕</button>
      </div>

      {claim && (
        <div className="dk__pane" data-pane="claim">
          <p className="dk__statement">{claim.statement || claim.title}</p>
          <div className="dk__standing">
            {claim.maturityLabel ?? claim.maturity} · {claim.evidence} piece{claim.evidence === 1 ? '' : 's'} of evidence
          </div>
          <GestureBar w={w} threadId={anchor.threadId}
                      subject={claim.statement || claim.title}
                      onFiled={onWorldStale} />
          {(claim.supportRefs?.length ?? 0) > 0 && (
            <div className="dk__evidence">
              {claim.supportRefs!.map(s => (
                <div className="dk__ev" key={s.id}>
                  {s.artifact && w.artifactBase && (
                    <a href={`${w.artifactBase}${s.artifact}`} target="_blank" rel="noreferrer">
                      <img src={`${w.artifactBase}${s.artifact}`} alt={s.title} />
                    </a>
                  )}
                  <span className="dk__evtitle">{s.title}</span>
                  <span className="dk__evtype">{s.type}</span>
                </div>
              ))}
            </div>
          )}
          {claim.caveats.length > 0 && (
            <div className="dk__caveats">caveats: {claim.caveats.join(' · ')}</div>
          )}
        </div>
      )}

      {anchor.kind === 'figure' && anchor.entityId && (
        <FigurePane w={w} entityId={anchor.entityId} title={anchor.title} />
      )}

      <div className="dk__body" ref={bodyRef}>
        {err && <div className="dk__err">could not load the transcript ({err})</div>}
        {!err && msgs === null && <div className="dk__err">loading…</div>}
        {!err && msgs !== null && msgs.length === 0 && (
          <div className="dk__err">nothing said on this line yet — the composer below starts it</div>
        )}
        {(msgs ?? []).map((m, i) => {
          const next = msgs?.[i + 1]
          const gapOuts = (m.steps ?? 0) > 0 && m.ts
            ? outputsBetween(w, anchor.threadId, m.ts, next?.ts ?? '9999')
            : []
          return (
            <div key={i} className={`dk__msg dk__msg--${m.role === 'user' ? 'you' : 'guide'}`}>
              <div className="dk__who">{m.role === 'user' ? 'you' : 'guide'} · {m.ts.slice(0, 16).replace('T', ' ')}
                {m.role !== 'user' && (
                  <PinButton w={w} threadId={anchor.threadId} text={m.text} ts={m.ts} />
                )}
              </div>
              <div className="dk__text">{mdBlocks(m.text)}</div>
              {(m.steps ?? 0) > 0 && (
                <div className="dk__steps" title="tool runs between messages — each is a sediment line">
                  · {m.steps} working step{(m.steps ?? 0) === 1 ? '' : 's'} ·
                </div>
              )}
              {gapOuts.length > 0 && (
                <div className="dk__outs">
                  {gapOuts.map(o => (
                    <a key={o.id} href={`${w.artifactBase}${o.artifact}`} target="_blank"
                       rel="noreferrer" title={o.title}>
                      <img src={`${w.artifactBase}${o.artifact}`} alt={o.title} />
                    </a>
                  ))}
                </div>
              )}
            </div>
          )
        })}
        {turn.phase === 'working' && (
          <div className="dk__working">▶ the guide is working — steps land above as they finish
            <button className="dk__cancel" onClick={cancel}>stop</button>
          </div>
        )}
        {turn.phase === 'awaiting' && (
          <div className="dk__awaiting">the guide is asking YOU — your next message answers it</div>
        )}
      </div>

      {sendErr && <div className="dk__senderr">could not send ({sendErr}) — draft kept</div>}
      <div className="dk__composer">
        <textarea
          value={draft}
          placeholder={anchor.kind === 'plan' ? 'the planned ask — edit, then send to launch'
            : anchor.entityId ? `ask about this ${anchor.kind} on its line…`
            : 'ask on this line — the guide answers here…'}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send() }
          }}
          rows={draft.length > 90 ? 4 : 2}
        />
        <div className="dk__cactions">
          <span className="dk__hint">⌘↵ sends · the turn runs on this line</span>
          <button className="dk__send" onClick={send}
                  disabled={!draft.trim() || turn.phase === 'working'}>
            {turn.phase === 'awaiting' ? 'answer' : anchor.kind === 'plan' ? '▷ launch' : 'send'}
          </button>
        </div>
      </div>

      {w.threadHrefBase && (
        <a className="dk__continue" target="_blank" rel="noreferrer"
           href={`${w.threadHrefBase}${anchor.threadId}${draft ? `?draft=${encodeURIComponent(draft)}` : ''}`}
           title="the full workspace on this line — richer tools, same thread">
          open in the workspace ↗
        </a>
      )}
    </aside>
  )
}

// ---------------------------------------------------------------- gestures
// The investigation family (§6): one-tap TYPED planned-item constructors.
// Gestures never execute — each writes a plan item on the claim's line,
// with its own ▷ work; the scrutiny ladder is requested by hand:
//   check — is it sound?  corroborate — is it real?
//   alternatives — is it rightly explained?  expand — grow the direction.

const GESTURES: { verb: string; hint: string; tpl: (s: string) => string }[] = [
  { verb: 'check', hint: 'is this result SOUND? error-hunting on the immediate analysis',
    tpl: s => `check the soundness of: ${s} — robustness, spec sensitivity, the mundane explanation` },
  { verb: 'corroborate', hint: 'is it REAL? an independent line of evidence — different method or data',
    tpl: s => `corroborate independently: ${s} — a different design, method, or data slice` },
  { verb: 'alternatives', hint: 'is it rightly EXPLAINED? rival hypotheses and the discriminating test',
    tpl: s => `rival explanations for: ${s} — enumerate them and design the discriminating test` },
  { verb: 'expand', hint: 'this is interesting — GROW the direction',
    tpl: s => `expand the direction opened by: ${s}` },
]

function GestureBar({ w, threadId, subject, onFiled }: {
  w: World; threadId: string; subject: string; onFiled?: () => void
}) {
  const [receipt, setReceipt] = useState<string | null>(null)
  const file = async (verb: string, tpl: (s: string) => string) => {
    const q = w.projectId ? `?project_id=${encodeURIComponent(w.projectId)}` : ''
    try {
      const short = subject.length > 140 ? `${subject.slice(0, 137)}…` : subject
      const r = await fetch(`${w.apiBase ?? ''}/api/threads/${threadId}/open-questions${q}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: tpl(short), source: 'user', kind: verb }),
      })
      if (!r.ok) throw new Error(`${r.status}`)
      setReceipt(`→ planned on this line · ${verb} — launch it from the section's plan`)
      onFiled?.()
    } catch (e) {
      setReceipt(`could not file (${String(e)})`)
    }
  }
  return (
    <div className="dk__gestures">
      {GESTURES.map(g => (
        <button key={g.verb} className="dk__gesture" title={g.hint}
                onClick={() => file(g.verb, g.tpl)}>
          {g.verb}
        </button>
      ))}
      {receipt && <div className="dk__receipt">{receipt}</div>}
    </div>
  )
}

// --------------------------------------------------------------------- pin
// THE universal curation gesture (§6): "this matters; don't lose it."
// A pinned answer files DIRECTLY as a note on this line — the user's own
// noticing needs no ratification — and the receipt is the button itself.

function PinButton({ w, threadId, text, ts }: {
  w: World; threadId: string; text: string; ts?: string
}) {
  const [state, setState] = useState<'idle' | 'pinned' | 'failed'>('idle')
  const pin = async () => {
    if (state === 'pinned') return
    const q = w.projectId ? `?project_id=${encodeURIComponent(w.projectId)}` : ''
    try {
      const r = await fetch(`${w.apiBase ?? ''}/api/record/pin${q}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: threadId, text, ...(ts ? { ts } : {}) }),
      })
      if (!r.ok) throw new Error(`${r.status}`)
      setState('pinned')
    } catch { setState('failed') }
  }
  return (
    <button className={`dk__pin ${state === 'pinned' ? 'dk__pin--on' : ''}`}
            onClick={pin}
            title={state === 'pinned'
              ? 'pinned — filed as a note on this line'
              : 'pin — this reasoning must not evaporate into the transcript; files as a note on this line, no ceremony'}>
      {state === 'pinned' ? '📌 pinned → note' : state === 'failed' ? 'pin ✗' : 'pin'}
    </button>
  )
}

// ------------------------------------------------------------- figure pane

function FigurePane({ w, entityId, title }: { w: World; entityId: string; title: string }) {
  const [prov, setProv] = useState<{ method?: { tool?: string; when?: string }
                                     entity?: { artifact?: string } } | null>(null)
  const [artifact, setArtifact] = useState<string | null>(null)
  useEffect(() => {
    fetch(`${w.apiBase ?? ''}/api/entities/${entityId}/provenance`)
      .then(r => r.ok ? r.json() : null)
      .then(p => {
        if (!p) return
        setProv(p)
        const ap = p?.entity?.artifact_path || p?.entity?.metadata?.artifact_path
        if (typeof ap === 'string') setArtifact(ap.split('/').pop() || null)
      })
      .catch(() => {})
  }, [w.apiBase, entityId])
  // the world's supports_index already resolved an image for this entity
  const idxArt = Object.values(w.claims)
    .flatMap(c => c.supportRefs ?? [])
    .find(s => s.id === entityId)?.artifact
  const img = idxArt || artifact
  const pidFromBase = w.threadHrefBase?.split('/')[2]
  return (
    <div className="dk__pane" data-pane="figure">
      {img && w.artifactBase && (
        <a href={`${w.artifactBase}${img}`} target="_blank" rel="noreferrer"
           title={`${title} — full size`}>
          <img className="dk__figimg" src={`${w.artifactBase}${img}`} alt={title} />
        </a>
      )}
      {prov?.method?.tool && (
        <div className="dk__prov">
          produced by {prov.method.tool}
          {prov.method.when ? ` · ${String(prov.method.when).slice(0, 10)}` : ''}
        </div>
      )}
      {pidFromBase && (
        <a className="dk__card" target="_blank" rel="noreferrer"
           href={`/p/${pidFromBase}/results/e/${entityId}`}
           title="the full card: revisions, notes, panels, interpretation">
          open the full card ↗
        </a>
      )}
    </div>
  )
}
