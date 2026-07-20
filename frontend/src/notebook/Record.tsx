/**
 * The Record — living-lab-notebook prototype (alt_uis.md §2).
 *
 * One scrollable, stratified document over the entity graph:
 *   what's new  →  THE STORY SO FAR (ratified narrative, per question)
 *               →  FIELD NOTES & TRAILS (noticed, not believed)
 *               →  SEDIMENT (every run, one line, automatic)
 *
 * Disciplines the prototype demonstrates:
 *  - every rendered fact is a live view over an entity (inline ref chips,
 *    figure embeds with provenance), never pasted text
 *  - ratified prose is immutable; the agent appends dated ADDENDA proposals
 *  - downward disclosure: figure → run → code / params / env / log
 *  - margin bench: chat invoked ON an element, element already in focus
 *  - methods mode per section (provenance-generated methods detail inline)
 *  - the prolific/rare asymmetry: a 104-output QC run is ONE sediment line
 *  - focus spectrum: the same machinery renders the p-value visitor's
 *    one-pager (view toggle) — no modes, no minimum thickness
 *
 * WORK LOOP (the storyboard's subject): the document is where you stand,
 * sessions are where you reach. A working panel opens OVER the document,
 * scoped by where you summoned it (project / question / trail / figure);
 * runs land in the sediment at launch; session close distills; the
 * transcript files under its anchor. Renders from World.desk / World.panel /
 * World.archive — absent in the plain notebook, present in storyboard
 * scenes.
 *
 * The renderer is parameterized over a World (see world.ts): /notebook.html
 * renders coastalWorld; /workflow.html renders storyboard scenes.
 */
import { Fragment as F, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { type Section, type Trail, type SedimentEntry } from './fixture'
import { coastalWorld, type World, type SessionRec } from './world'
import WorkPanel from './WorkPanel'
import SessionPage from './SessionPage'

const ART = (id: string) => `/artifacts/coastal/${id}.svg`

const MATURITY_GLYPH: Record<string, string> = {
  conjecture: '○', supported: '◐', 'cross-checked': '◕', robust: '●', contested: '◮',
}

// ---------------------------------------------------------------- ref parsing

interface RefCtx {
  w: World
  openBench: (id: string, label: string) => void
  toggleDisclose: (id: string) => void
  disclosed: Set<string>
  scrollTo: (domId: string) => void
  /** open a session — its full page by default, at a specific turn when known */
  openSession: (id: string, turn?: number) => void
  /** deixis, doc → chat: clicking an element makes it the conversation's subject */
  look: (label: string) => void
  /** hold an excerpt on the desk for the session's duration (two-locus work) */
  hold: (elId: string, label: string) => void
}

/** Render prose with [[kind:id|label]] live references. Block-level
 *  [[figure:id]] tokens are handled by splitBlocks() before this runs. */
function renderRefs(text: string, ctx: RefCtx): ReactNode[] {
  const { w } = ctx
  const out: ReactNode[] = []
  const re = /\[\[(fig|claim|run|trail):([^\]|]+)(?:\|([^\]]+))?\]\]/g
  let last = 0, m: RegExpExecArray | null, k = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(<F key={k++}>{text.slice(last, m.index)}</F>)
    const [, kind, id, label] = m
    if (kind === 'fig') {
      out.push(
        <button key={k++} className="ref ref--fig" title={`${w.figureTitles[id] ?? id} — click to open the figure and its provenance`}
                onClick={() => ctx.toggleDisclose(id)}>
          {label ?? w.figureTitles[id] ?? id}
        </button>)
    } else if (kind === 'claim') {
      const c = w.claims[id]
      out.push(
        <button key={k++} className="ref ref--claim"
                title={c ? `${c.maturity} · ${c.evidence} evidence · caveats: ${c.caveats.join('; ')}` : id}
                onClick={() => ctx.openBench(id, c?.title ?? id)}>
          <span className="ref__dot">{MATURITY_GLYPH[c?.maturity ?? 'conjecture']}</span>
          {label ?? c?.title ?? id}
          <span className="ref__mat">{c?.maturity}</span>
        </button>)
    } else if (kind === 'run') {
      out.push(
        <button key={k++} className="ref ref--run" title="jump to this run in the sediment"
                onClick={() => ctx.scrollTo(`el-${id}`)}>
          {label ?? id}{id === 'run_holdout' ? <span className="ref__live">▶</span> : null}
        </button>)
    } else if (kind === 'trail') {
      const t = w.trails.find(x => x.id === id)
      out.push(
        <button key={k++} className="ref ref--trail" title={t ? `trail: ${t.title} (${t.fragments.length} fragments)` : id}
                onClick={() => ctx.scrollTo(`el-${id}`)}>
          ⋱ {label ?? id}
        </button>)
    }
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(<F key={k++}>{text.slice(last)}</F>)
  return out
}

/** Split prose into text blocks and block-level [[figure:id]] embeds. */
function splitBlocks(text: string): { kind: 'text' | 'figure'; value: string }[] {
  const parts: { kind: 'text' | 'figure'; value: string }[] = []
  const re = /\[\[figure:([^\]]+)\]\]/g
  let last = 0, m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push({ kind: 'text', value: text.slice(last, m.index) })
    parts.push({ kind: 'figure', value: m[1] })
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push({ kind: 'text', value: text.slice(last) })
  return parts
}

// ------------------------------------------------------------- figure + prov

function ProvDrawer({ figId, ctx }: { figId: string; ctx: RefCtx }) {
  const p = ctx.w.provenance[figId]
  const [tab, setTab] = useState<'code' | 'params' | 'env' | 'log'>('code')
  if (!p) return null
  return (
    <div className="prov">
      <div className="prov__chain">
        <span className="prov__link">figure</span> ←{' '}
        <span className="prov__run">{p.runTitle}</span>
        <span className="prov__meta"> · {p.date} · {p.placement}</span>
        <span className="prov__meta"> · from {p.inputs.map(i => i.title).join(' + ')}</span>
      </div>
      <div className="prov__tabs">
        {(['code', 'params', 'env', 'log'] as const).map(t => (
          <button key={t} className={`prov__tab ${tab === t ? 'is-on' : ''}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      {tab === 'code' && <pre className="prov__pre">{p.code}</pre>}
      {tab === 'params' && (
        <pre className="prov__pre">{Object.entries(p.params).map(([k, v]) => `${k} = ${v}`).join('\n')}</pre>
      )}
      {tab === 'env' && (
        <pre className="prov__pre">{p.env.packages.join('\n')}{'\n'}{p.env.fingerprint}</pre>
      )}
      {tab === 'log' && <pre className="prov__pre">{p.log}</pre>}
    </div>
  )
}

function FigureEmbed({ figId, ctx, caption }: { figId: string; ctx: RefCtx; caption?: string }) {
  const open = ctx.disclosed.has(figId)
  const title = ctx.w.figureTitles[figId] ?? figId
  return (
    <figure className="fig" id={`el-${figId}`}>
      <img src={ART(figId)} alt={title} onClick={() => ctx.look(title)}
           title="click to make this the conversation's subject (looking at:)" />
      <figcaption>
        <span>{caption ?? title}</span>
        <span className="fig__actions">
          <button onClick={() => ctx.toggleDisclose(figId)} title="the technical record: producing run, code, params, environment, log">
            {open ? 'close ▴' : 'how was this made? ▾'}
          </button>
          <button onClick={() => ctx.openBench(figId, title)} title="open the margin bench on this element">
            ask ✦
          </button>
        </span>
      </figcaption>
      {open && <ProvDrawer figId={figId} ctx={ctx} />}
    </figure>
  )
}

// ------------------------------------------------------------------ sections

function NarrativeSection({ s, ctx, methods, onMethods, onRatify, ratified, onWork }: {
  s: Section; ctx: RefCtx
  methods: boolean; onMethods: () => void
  onRatify: (id: string) => void
  ratified: Set<string>
  onWork?: (sectionId: string) => void
}) {
  const { w } = ctx
  const phaseNote = { early: 'early — mostly noticing', mid: 'mid — condensing', late: 'late — writing up' }[s.phase]
  // the live session's home locus wears a STANDING state — scroll away and
  // back, and where the work is landing stays unmistakable
  const anchored = w.anchorAt?.elId === s.id
  return (
    <section className={`nsec ${anchored ? 'nsec--live' : ''}`} id={`el-${s.id}`}>
      {anchored && (
        <div className="nsec__livetag" title="this session's anchor — its products land here first; the state stands until the session closes">
          ⟲ {w.anchorAt!.session} · working here
        </div>
      )}
      <div className="nsec__head">
        <h3>{s.question}</h3>
        <span className="nsec__phase" title="phase is per-question, derived from content — a young question in an old project is simply early">{phaseNote}</span>
        {s.paragraphs.length > 0 && (
          <button className={`nsec__methods ${methods ? 'is-on' : ''}`} onClick={onMethods}
                  title="expand every referenced result into its methods detail — generated from provenance, never hand-maintained">
            methods mode
          </button>
        )}
        {w.work && (
          <button className="nsec__work" onClick={() => onWork?.(s.id)}
                  title="open a working session on this question — the agent starts with the question, its evidence, and its trails already in scope">
            work ▸
          </button>
        )}
      </div>
      {s.sessions && s.sessions.length > 0 && (
        <div className="nsec__sessions">
          {s.sessions.map(x => (
            <button key={x.label} className="sess" onClick={() => ctx.openSession(x.label)}
                    title="the working exchange, filed under this question — the full episode: transcript, artifacts, leftovers; continuable">
              ⟲ {x.label} · {x.when} · {x.meta} — transcript
            </button>
          ))}
        </div>
      )}
      {s.paragraphs.length === 0 && (
        <div className="nsec__stub">
          <p>Nothing ratified yet — the story is written from evidence, not ahead of it.</p>
          {s.open && s.open.length > 0 && (
            <ul className="nsec__open">
              {s.open.map(o => <li key={o}>{o}</li>)}
            </ul>
          )}
        </div>
      )}
      {s.paragraphs.map(p => (
        <div className="npara" key={p.id} id={`el-${p.id}`}>
          {splitBlocks(p.text).map((b, i) =>
            b.kind === 'figure'
              ? <FigureEmbed key={i} figId={b.value} ctx={ctx} />
              : <p key={i}>{renderRefs(b.value, ctx)}</p>)}
          <div className="npara__sig" title="ratified prose is immutable — the agent may propose, only you may write">
            {p.ratified.draftedBy ? `drafted by ${p.ratified.draftedBy} · ` : ''}ratified by {p.ratified.by} · {p.ratified.on}
          </div>
          {methods && (
            <div className="npara__methods">
              {extractFigRefs(p.text).map(fid => (
                <div key={fid} className="npara__method">
                  <span className="npara__method-fig">{w.figureTitles[fid] ?? fid}:</span>{' '}
                  {w.provenance[fid]
                    ? `${w.provenance[fid].runTitle} — ${Object.entries(w.provenance[fid].params).map(([k, v]) => `${k}=${v}`).join(', ')} · ${w.provenance[fid].env.packages.join(', ')} · ${w.provenance[fid].env.fingerprint}`
                    : 'no exec record'}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
      {s.addenda.map(a => {
        const isRat = ratified.has(a.id) || a.status === 'ratified'
        return (
          <div key={a.id} id={`el-${a.id}`} className={`addendum ${isRat ? 'addendum--ratified' : ''}`}>
            <div className="addendum__tag">
              {isRat ? `addendum · ${a.on} · ratified` : `addendum proposed by Guide · ${a.on} — ratified prose is never rewritten; updates append`}
            </div>
            <p>{renderRefs(a.text, ctx)}</p>
            {!isRat && (
              <div className="addendum__actions">
                <button className="btn btn--primary" onClick={() => onRatify(a.id)}>Ratify</button>
                <button className="btn" title="dismissals are remembered">Dismiss</button>
                <button className="btn" onClick={() => ctx.openBench(a.id, `addendum — ${s.question}`)}>discuss ✦</button>
                {w.work && (
                  <button className="btn" onClick={() => ctx.hold(`el-${a.id}`, `addendum · ${a.on}`)}
                          title="hold this on the desk while you work elsewhere — two-locus work without split screen; clears at session close">
                    hold ⌖
                  </button>
                )}
              </div>
            )}
          </div>
        )
      })}
    </section>
  )
}

function extractFigRefs(text: string): string[] {
  const ids: string[] = []
  const re = /\[\[(?:fig|figure):([^\]|]+)(?:\|[^\]]+)?\]\]/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) ids.push(m[1])
  return ids
}

// -------------------------------------------------------------------- trails

function TrailCard({ t, ctx, drafted, onDraft }: {
  t: Trail; ctx: RefCtx; drafted: boolean; onDraft: () => void
}) {
  const stateWord = { accumulating: 'accumulating', cohering: 'cohering ●', stalled: 'stalled' }[t.state]
  return (
    <article className={`trail trail--${t.state}`} id={`el-${t.id}`}>
      <div className="trail__head">
        <span className="trail__id">{t.id}</span>
        <h4 onClick={() => ctx.look(`trail ${t.id} · ${t.title}`)}
            title="click to make this trail the conversation's subject">{t.title}</h4>
        <span className={`trail__state trail__state--${t.state}`}
              title="a trail is a named hunch — a home for weak scattered evidence BEFORE it can be stated as a claim">
          {stateWord}
        </span>
        <button className="trail__ask" onClick={() => ctx.openBench(t.id, `trail ${t.id}: ${t.title}`)}>ask ✦</button>
      </div>
      <div className="trail__frags">
        {t.fragments.map((f, i) => (
          <div key={i} className={`frag ${f.counter ? 'frag--counter' : ''}`}>
            <span className="frag__ts">{f.ts}</span>
            <span className="frag__text">
              {f.counter && <span className="frag__counterlabel" title="trails keep counter-examples too — that is what makes them honest">counter · </span>}
              {f.text}
              {f.ref && (
                <button className="ref ref--fig frag__ref" onClick={() => ctx.toggleDisclose(f.ref!)}>
                  {ctx.w.figureTitles[f.ref] ?? f.ref}
                </button>
              )}
              {f.draft && <span className="draftb" title="drafted by the agent during a working session — enters the trail when you ratify it">draft</span>}
              {f.src && (
                <button className="frag__turn" onClick={() => ctx.openSession(f.src!.sess, f.src!.turn)}
                        title="provenance for prose: the exchange that drafted this fragment">
                  ⟲ turn {f.src.turn}
                </button>
              )}
            </span>
          </div>
        ))}
        {t.fragments.filter(f => f.ref).map(f => ctx.disclosed.has(f.ref!) && (
          <div key={f.ref} className="trail__figopen">
            <FigureEmbed figId={f.ref!} ctx={ctx} />
          </div>
        ))}
      </div>
      {t.nudge && !drafted && (
        <div className="trail__nudge">
          <span title="the agent watches trails for coherence — recurrence is the threshold, not one-off pattern-matching">✦ {t.nudge.text}</span>
          <button className="btn btn--primary" onClick={onDraft}>{t.nudge.action}</button>
          <button className="btn">not yet</button>
        </div>
      )}
      {t.nudge && drafted && (
        <div className="trail__nudge trail__nudge--done">
          ✓ claim drafted — waiting in your inbox as a proposal (nothing entered the record yet)
        </div>
      )}
    </article>
  )
}

// ------------------------------------------------------------------ sediment

function SedimentRow({ e, ctx, open, onToggle }: {
  e: SedimentEntry; ctx: RefCtx; open: boolean; onToggle: () => void
}) {
  const ret = {
    kept: { label: `kept ✓${e.site ? ` on ${e.site}` : ''}`, cls: 'ok' },
    temporary: { label: 'temporary', cls: 'mut' },
    'at-risk': { label: 'at risk', cls: 'risk' },
  }[e.retention]
  return (
    <div className={`sed ${e.state === 'failed' ? 'sed--failed' : ''} ${e.isNew ? 'sed--new' : ''}`} id={`el-${e.id}`}>
      <button className="sed__line" onClick={onToggle} title={open ? 'collapse' : `expand ${e.nOutputs} outputs`}>
        <span className="sed__date">{e.date}</span>
        <span className={`sed__state sed__state--${e.state}`}>
          {e.state === 'running' ? '▶' : e.state === 'failed' ? '✗' : '·'}
        </span>
        <span className="sed__title">{e.title}</span>
        <span className="sed__verdict">{e.verdict}</span>
        <span className="sed__n">{e.nOutputs > 0 ? `${e.nOutputs} outputs` : ''}</span>
        {e.trailRef && <span className="sed__trail" title={`feeds trail ${e.trailRef}`}>⋱ {e.trailRef}</span>}
        {e.sessionRef && (
          <span className="sed__sess"
                title={`produced in session “${e.sessionRef}”${e.turnRef ? ` — jump to turn ${e.turnRef}` : ' — open the session'}`}
                onClick={ev => { ev.stopPropagation(); ctx.openSession(e.sessionRef!, e.turnRef) }}>
            ⟲ {e.sessionRef}
          </span>
        )}
        <span className={`sed__ret sed__ret--${ret.cls}`}>{ret.label}</span>
      </button>
      {open && e.shown.length > 0 && (
        <div className="sed__grid">
          {e.shown.map(o => (
            <button key={o.id} className={`sed__thumb ${o.flagged ? 'sed__thumb--flag' : ''}`}
                    onClick={() => ctx.toggleDisclose(o.id)}
                    title={o.flagged ? `${o.title} — flagged by QC` : o.title}>
              <img src={ART(o.id)} alt={o.title} />
              <span>{o.flagged ? '⚑ ' : ''}{o.title}</span>
            </button>
          ))}
          {e.nOutputs > e.shown.length && (
            <div className="sed__more">+{e.nOutputs - e.shown.length} more outputs — none demanded reading; nothing was lost</div>
          )}
        </div>
      )}
      {open && e.shown.map(o => ctx.disclosed.has(o.id) && (
        <div key={o.id} className="sed__figopen"><FigureEmbed figId={o.id} ctx={ctx} /></div>
      ))}
    </div>
  )
}

// -------------------------------------------------------------- margin bench

function MarginBench({ w, target, onClose }: {
  w: World; target: { id: string; label: string }; onClose: () => void
}) {
  const canned = w.bench[target.id] ?? w.benchFallback
  const [extra, setExtra] = useState<{ role: 'you' | 'guide'; text: string }[]>([])
  const [draft, setDraft] = useState('')
  const send = () => {
    if (!draft.trim()) return
    setExtra(x => [...x, { role: 'you', text: draft.trim() },
      { role: 'guide', text: 'Canned in the prototype — but note what did NOT happen: you never had to say which element you meant. The margin opened with it focused.' }])
    setDraft('')
  }
  return (
    <aside className="bench">
      <div className="bench__head">
        <div>
          <div className="bench__kicker">margin bench — transient, anchored</div>
          <div className="bench__target" title="the element this conversation is attached to">{target.label}</div>
        </div>
        <button className="bench__close" onClick={onClose} title="the exchange stays reachable from the element's history; distilled outcomes return as drafts">✕</button>
      </div>
      <div className="bench__msgs">
        {[...canned, ...extra].map((m, i) => (
          <div key={i} className={`bmsg bmsg--${m.role}`}>
            <span className="bmsg__who">{m.role === 'you' ? 'you' : 'Guide'}</span>
            <p>{m.text}</p>
          </div>
        ))}
      </div>
      <div className="bench__foot">
        <input value={draft} placeholder={`Ask about this…`}
               onChange={e => setDraft(e.target.value)}
               onKeyDown={e => { if (e.key === 'Enter') send() }} />
        <button className="btn btn--primary" onClick={send}>↑</button>
      </div>
      <div className="bench__note">outcomes distill back into the record as drafts; the raw exchange never clutters the document</div>
    </aside>
  )
}

// -------------------------------------------------------------------- search

interface Hit {
  label: string; stratum: string
  domId?: string
  sess?: { id: string; turn?: number }
}
function searchRecord(w: World, q: string, scope: 'story' | 'noticed' | 'everything'): Hit[] {
  const needle = q.toLowerCase()
  const hits: Hit[] = []
  const has = (s: string) => s.toLowerCase().includes(needle)
  if (scope === 'story' || scope === 'everything') {
    for (const s of w.sections) {
      for (const p of s.paragraphs) if (has(p.text) || has(s.question)) {
        hits.push({ domId: `el-${p.id}`, label: `${s.question} — §`, stratum: 'story' }); break
      }
      for (const a of s.addenda) if (has(a.text)) hits.push({ domId: `el-${a.id}`, label: `addendum (${s.question})`, stratum: 'story' })
    }
  }
  if (scope === 'noticed' || scope === 'everything') {
    for (const t of w.trails) if (has(t.title) || t.fragments.some(f => has(f.text)))
      hits.push({ domId: `el-${t.id}`, label: `${t.id} · ${t.title}`, stratum: 'noticed' })
    for (const n of w.looseNotes) if (has(n.text))
      hits.push({ domId: `el-note-${n.id}`, label: n.text.slice(0, 48) + '…', stratum: 'noticed' })
  }
  if (scope === 'everything') {
    for (const e of w.sediment) if (has(e.title) || has(e.verdict))
      hits.push({ domId: `el-${e.id}`, label: `${e.date} · ${e.title}`, stratum: 'sediment' })
    // what was SAID, not just what was kept — episodic recall is a first-class
    // entry path ("did we ever discuss…" lands on the turn)
    for (const s of w.sessions ?? []) {
      let turn = 0
      for (const m of s.msgs) {
        const isTurn = !!m.text && (m.role === 'you' || m.role === 'guide')
        if (isTurn) turn++
        if (m.text && has(m.text)) {
          const at = m.text.toLowerCase().indexOf(needle)
          const snippet = m.text.slice(Math.max(0, at - 12), at + needle.length + 24)
          hits.push({
            sess: { id: s.id, turn: isTurn ? turn : Math.max(1, turn) },
            label: `“…${snippet}…” — ${s.label} · ${s.when}`,
            stratum: 'session',
          })
          break
        }
      }
    }
  }
  return hits.slice(0, 8)
}

// ----------------------------------------------------------------- desk strip

/** Glyph grammar (uniform everywhere): ⟲ marks a DOOR to a session — any
 *  state; ▶ means EXECUTING NOW — a state marker, never a link icon. They
 *  co-occur when a door leads to a live session. */
function DeskStrip({ w, held, onOpenSession, onJump }: {
  w: World
  held: { elId: string; label: string }[]
  onOpenSession: (id: string) => void
  onJump: (elId: string) => void
}) {
  const d = w.desk!
  return (
    <div className="desk" title="the present tense: open sessions, running work, held excerpts, where you left off">
      <span className="desk__kicker">at the desk</span>
      <span className="desk__line">{d.line}</span>
      {d.items.map(i => (
        <button key={i.label} className={`desk__item ${i.live ? 'is-live' : ''}`}
                onClick={() => { if (i.sessionId) onOpenSession(i.sessionId) }}>
          {i.sessionId ? '⟲ ' : ''}{i.label}{i.live ? <span className="desk__running" title="session live now"> ▶</span> : ''}
          <span className="desk__meta"> · {i.meta}</span>
          {i.action && <span className="desk__act"> {i.action}</span>}
        </button>
      ))}
      {held.map(h => (
        <button key={h.elId} className="desk__item desk__item--held" onClick={() => onJump(h.elId)}
                title="held for this session — click to jump back; clears at session close">
          ⌖ {h.label}
        </button>
      ))}
    </div>
  )
}

/** The delta rail — a minimap of change. Ticks sit at each changed
 *  element's position in the document; three tiers only. Click = jump.
 *  The rail may glow; the page never scrolls itself. */
function DeltaRail({ w, docRef, onJump }: {
  w: World
  docRef: React.RefObject<HTMLElement | null>
  onJump: (elId: string) => void
}) {
  const [ticks, setTicks] = useState<{ pct: number; kind: string; elId: string; label: string }[]>([])
  useEffect(() => {
    const doc = docRef.current
    if (!doc || !w.deltas?.length) { setTicks([]); return }
    const t = setTimeout(() => {
      const total = doc.scrollHeight
      setTicks(w.deltas!.map(d => {
        const el = document.getElementById(d.elId)
        if (!el) return null
        const top = el.getBoundingClientRect().top - doc.getBoundingClientRect().top + doc.scrollTop
        return { pct: Math.min(97, (top / total) * 100), kind: d.kind, elId: d.elId, label: d.label }
      }).filter(Boolean) as { pct: number; kind: string; elId: string; label: string }[])
    }, 120)   // after layout (images have known aspect from CSS; 120ms suffices for the mock)
    return () => clearTimeout(t)
  }, [w, docRef])
  if (!ticks.length) return null
  return (
    <div className="drail-wrap">
      <div className="drail" title="the delta rail — where change landed, by kind; click a tick to jump">
        {ticks.map(t => (
          <button key={t.elId + t.kind} className={`drail__tick drail__tick--${t.kind}`}
                  style={{ top: `${t.pct}%` }} title={t.label}
                  onClick={() => onJump(t.elId)} />
        ))}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ day zero

/** The day-0 face: a new project is a composer, not a document. */
function BareStart({ w, onAdvance }: { w: World; onAdvance?: (t: string) => void }) {
  const [draft, setDraft] = useState('')
  return (
    <div className="bare">
      <div className="bare__box">
        <h1>{w.project.title}</h1>
        <div className="bare__sub">a record co-written by you and Guide · started today</div>
        <div className="bare__composer">
          <input autoFocus value={draft}
                 placeholder="What are we working with? Describe the study, point at data, or ask the first question…"
                 onChange={e => setDraft(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') onAdvance?.('start') }} />
          <button className="btn btn--primary" onClick={() => onAdvance?.('start')}>begin ↑</button>
        </div>
        <div className="bare__note">
          There is nothing to set up and nothing to fill in. The document will build
          itself from the work — the first run lands in the sediment the moment it
          launches, notes accrete as you notice things, and the story is written
          from evidence later. This box is the whole interface.
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------- root

export default function Record(props: { world?: World; onAdvance?: (t: string) => void }) {
  const w = props.world ?? coastalWorld
  if (w.bare) return <BareStart w={w} onAdvance={props.onAdvance} />
  return <RecordDoc w={w} onAdvance={props.onAdvance} />
}

function RecordDoc({ w, onAdvance }: { w: World; onAdvance?: (t: string) => void }) {
  const [benchFor, setBenchFor] = useState<{ id: string; label: string } | null>(null)
  // a session renders full-page (sifting/review) or docked in the right
  // column (side-by-side working mode) — each converts into the other
  const [sessPage, setSessPage] = useState<{ id: string; turn?: number } | null>(w.openSession ?? null)
  const [sessDock, setSessDock] = useState<string | null>(null)
  const [grain, setGrain] = useState<'run' | 'session'>(w.sedimentGrain ?? 'run')
  const [lookingAt, setLookingAt] = useState<string | undefined>(undefined)
  const [held, setHeld] = useState<{ elId: string; label: string }[]>([])
  const docRef = useRef<HTMLElement | null>(null)
  const [disclosed, setDisclosed] = useState<Set<string>>(new Set())
  const [methodsOn, setMethodsOn] = useState<Set<string>>(new Set())
  const [openSed, setOpenSed] = useState<Set<string>>(new Set(w.openSediment ?? []))
  const [ratified, setRatified] = useState<Set<string>>(new Set())
  const [drafted, setDrafted] = useState<Set<string>>(new Set())
  const [view, setView] = useState<'record' | 'onepager'>(
    () => new URLSearchParams(window.location.search).get('view') === 'onepager' && w.onePager ? 'onepager' : 'record')
  const [newOpen, setNewOpen] = useState(false)
  const [q, setQ] = useState('')
  const [scope, setScope] = useState<'story' | 'noticed' | 'everything'>('everything')
  const [activeAnchor, setActiveAnchor] = useState('')

  // TOC tracks the reader's position: the anchor nearest above the upper
  // third of the document column is "where you are".
  const ANCHORS = [
    ...w.sections.map(s => `el-${s.id}`),
    ...w.trails.map(t => `el-${t.id}`),
    ...(w.looseNotes.length ? ['el-loose'] : []),
    'el-sediment',
  ]
  const onDocScroll = (e: React.UIEvent<HTMLElement>) => {
    const doc = e.currentTarget
    const line = doc.getBoundingClientRect().top + doc.clientHeight / 3
    let best = ''
    for (const id of ANCHORS) {
      const el = document.getElementById(id)
      if (el && el.getBoundingClientRect().top <= line) best = id
    }
    if (best !== activeAnchor) setActiveAnchor(best)
  }

  const scrollTo = (domId: string) => {
    const el = document.getElementById(domId)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('flash')
    setTimeout(() => el.classList.remove('flash'), 1600)
  }
  const toggle = (set: Set<string>, id: string) => {
    const n = new Set(set); if (n.has(id)) n.delete(id); else n.add(id); return n
  }
  const findSession = (id: string): SessionRec | undefined =>
    (w.sessions ?? []).find(s => s.id === id || s.label === id)
  const ctx: RefCtx = {
    w,
    openBench: (id, label) => setBenchFor({ id, label }),
    toggleDisclose: id => setDisclosed(s => toggle(s, id)),
    disclosed,
    scrollTo,
    openSession: (id, turn) => { if (findSession(id)) { setSessDock(null); setSessPage({ id, turn }) } },
    look: label => setLookingAt(label),
    hold: (elId, label) => setHeld(h => h.some(x => x.elId === elId) ? h : [...h, { elId, label }]),
  }
  const hits = useMemo(() => (q.trim() ? searchRecord(w, q, scope) : []), [w, q, scope])
  const behindLine = w.pendingDrafts > 0
    ? `${w.pendingDrafts} draft${w.pendingDrafts === 1 ? '' : 's'} waiting — the record is ~${Math.max(1, Math.round(w.pendingDrafts * 1.2))} day${Math.round(w.pendingDrafts * 1.2) === 1 ? '' : 's'} behind the work`
    : 'the record is current'

  // ------------------------------------------------ one-pager (§2.4)
  if (view === 'onepager' && w.onePager) {
    const op = w.onePager
    return (
      <div className="rec rec--onepager">
        <main className="doc doc--onepager">
          <div className="doc__viewbar">
            <button className="btn" onClick={() => setView('record')}>← full record</button>
            <span className="doc__viewnote">the p-value visitor's render — same machinery, thin project; nothing was imposed to get here</span>
          </div>
          <h1>{w.project.title}</h1>
          <p className="op__data"><b>Data.</b> {op.dataLine}</p>
          <p className="op__method"><b>Method.</b> {op.methodLine}</p>
          <div className="op__number">{op.number}</div>
          <p className="op__caveat"><b>Caveat.</b> {op.caveat}</p>
          <div className="op__sig">assembled from the record · {w.sediment.length} runs in the sediment appendix · print and take it to the meeting</div>
          <section className="op__appendix">
            <h2>Sediment appendix</h2>
            {w.sediment.map(e => (
              <div key={e.id} className="op__sedline">
                <span className="sed__date">{e.date}</span> {e.title} — {e.verdict}
              </div>
            ))}
          </section>
        </main>
      </div>
    )
  }

  // right column: the margin bench wins (same instrument, narrower scope);
  // otherwise a docked session transcript; otherwise the live session.
  const docked = sessDock ? findSession(sessDock) : undefined
  const rightPanel = benchFor
    ? <MarginBench w={w} key={`b-${benchFor.id}`} target={benchFor} onClose={() => setBenchFor(null)} />
    : docked
      ? <WorkPanel key={`d-${docked.id}`} continuable
          panel={{ archived: { label: docked.label, when: docked.when }, scope: docked.scope, msgs: docked.msgs }}
          onExpand={() => { setSessDock(null); setSessPage({ id: docked.id }) }}
          onClose={() => setSessDock(null)} />
      : w.panel
        ? <WorkPanel key="live" panel={w.panel} onAdvance={onAdvance}
            lookingAt={lookingAt}
            onShowRef={elId => scrollTo(elId)} />
        : null
  // peripheral change signals on the TOC: pulse badges, three tiers
  const deltaBadge = (elId: string) => {
    const d = w.deltas?.find(x => x.elId === elId)
    if (!d) return null
    return (
      <span className={`toc__delta toc__delta--${d.kind}`} title={d.label}>
        {d.kind === 'condition' ? '⚡' : `+${d.count ?? 1}`}
      </span>
    )
  }

  // a session's full page replaces the document column (the TOC stays);
  // ⇥ sends it to the right column and brings the document back
  const pageSess = sessPage ? findSession(sessPage.id) : undefined
  const showRight = pageSess ? (benchFor ? rightPanel : null) : rightPanel
  const recCls = benchFor ? 'rec rec--bench' : showRight ? 'rec rec--work' : 'rec'

  return (
    <div className={recCls}>
      {/* ---------- contents rail ---------- */}
      <nav className="toc">
        <div className="toc__title">The Record</div>
        <div className="toc__project">{w.project.title}</div>
        <div className="toc__since">since {w.project.started.slice(0, 7)}</div>

        {w.sections.length > 0 && <div className="toc__group">story so far</div>}
        {w.sections.map(s => (
          <button key={s.id} className={`toc__item ${activeAnchor === `el-${s.id}` ? 'is-active' : ''}`} onClick={() => scrollTo(`el-${s.id}`)}>
            <span className={`toc__phase toc__phase--${s.phase}`} />
            {s.question}
            {deltaBadge(`el-${s.id}`)}
          </button>
        ))}
        {(w.trails.length > 0 || w.looseNotes.length > 0) && <div className="toc__group">field notes</div>}
        {w.trails.map(t => (
          <button key={t.id} className={`toc__item ${activeAnchor === `el-${t.id}` ? 'is-active' : ''}`} onClick={() => scrollTo(`el-${t.id}`)}>
            <span className="toc__trail">⋱</span> {t.id} · {t.title}
            {deltaBadge(`el-${t.id}`)}
          </button>
        ))}
        {w.looseNotes.length > 0 && (
          <button className={`toc__item ${activeAnchor === 'el-loose' ? 'is-active' : ''}`} onClick={() => scrollTo('el-loose')}>
            <span className="toc__trail">·</span> loose notes
          </button>
        )}
        <div className="toc__group">sediment</div>
        <button className={`toc__item ${activeAnchor === 'el-sediment' ? 'is-active' : ''}`} onClick={() => scrollTo('el-sediment')}>
          {w.sediment.length} run{w.sediment.length === 1 ? '' : 's'} · complete · automatic
          {deltaBadge('el-sediment')}
        </button>

        <div className="toc__spacer" />
        {w.onePager && (
          <button className="toc__onepager" onClick={() => setView('onepager')}
                  title="the same record rendered for the one-number visitor (§ focus spectrum)">
            view as one-pager
          </button>
        )}
        <div className="toc__search">
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="search the record…" />
          <div className="toc__scopes">
            {(['story', 'noticed', 'everything'] as const).map(s => (
              <button key={s} className={`toc__scope ${scope === s ? 'is-on' : ''}`}
                      title={{ story: 'what did we conclude — narrative only', noticed: 'did we ever see… — notes + trails', everything: 'full record incl. sediment' }[s]}
                      onClick={() => setScope(s)}>{s}</button>
            ))}
          </div>
          {hits.length > 0 && (
            <div className="toc__hits">
              {hits.map((h, i) => (
                <button key={i} className="toc__hit"
                        onClick={() => {
                          if (h.sess) ctx.openSession(h.sess.id, h.sess.turn)
                          else if (h.domId) {
                            // the doc must be back on stage before we can scroll it
                            setSessPage(null)
                            const id = h.domId
                            setTimeout(() => scrollTo(id), 80)
                          }
                          setQ('')
                        }}>
                  <span className={`toc__hit-stratum toc__hit-stratum--${h.stratum}`}>{h.stratum}</span>
                  {h.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </nav>

      {/* ---------- the document (or a session's full page) ---------- */}
      {pageSess ? (
        <SessionPage key={`${pageSess.id}-${sessPage?.turn ?? ''}`} sess={pageSess} focusTurn={sessPage?.turn}
          onBack={() => setSessPage(null)}
          onDock={() => { setSessDock(pageSess.id); setSessPage(null) }} />
      ) : (
      <main className="doc" onScroll={onDocScroll} ref={docRef}>
        <DeltaRail w={w} docRef={docRef} onJump={elId => scrollTo(elId)} />
        <header className="doc__head">
          <h1>{w.project.title}</h1>
          <div className="doc__sub">
            a record co-written by you and Guide · narrative is yours to ratify · sediment keeps itself
          </div>
        </header>

        {/* the present tense: open sessions, held excerpts, where you left off */}
        {w.desk && <DeskStrip w={w} held={held} onOpenSession={id => ctx.openSession(id)} onJump={elId => scrollTo(elId)} />}

        {/* what's new */}
        {w.whatsNew && (
          <section className={`wnew ${newOpen ? 'is-open' : ''}`}>
            <button className="wnew__head" onClick={() => setNewOpen(o => !o)}>
              <span className="wnew__count">what's new since {w.whatsNew.since} · {w.whatsNew.items.length}</span>
              <span className="wnew__peek">
                {w.whatsNew.items.filter(i => i.loud || i.live).map((i, k) => (
                  <span key={k} className={`wnew__chip ${i.loud ? 'is-loud' : ''} ${i.live ? 'is-live' : ''}`}>
                    {i.live ? '▶ ' : i.loud ? '⚡ ' : ''}
                    {i.loud ? i.text.split(' (')[0] : i.text.split(' — ')[0]}
                  </span>
                ))}
              </span>
              <span className="wnew__behind" title="degradation is visible and recoverable, never silent rot">{behindLine}</span>
              <span>{newOpen ? '▾' : '▸'}</span>
            </button>
            {newOpen && (
              <div className="wnew__body">
                {w.whatsNew.items.map((i, k) => (
                  <div key={k} className={`wnew__item ${i.loud ? 'is-loud' : ''}`}>
                    <span className="wnew__ts">{i.ts}</span>{i.text}
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {/* ---------- stratum 1: narrative ---------- */}
        {w.sections.length > 0 && (
          <div className="stratum">
            <div className="stratum__rule"><span>the story so far</span><em>ratified · sparse · load-bearing</em></div>
            {w.sections.map(s => (
              <NarrativeSection key={s.id} s={s} ctx={ctx}
                methods={methodsOn.has(s.id)}
                onMethods={() => setMethodsOn(x => toggle(x, s.id))}
                onRatify={id => setRatified(x => new Set(x).add(id))}
                ratified={ratified}
                onWork={id => onAdvance?.(`work:${id}`)} />
            ))}
          </div>
        )}

        {/* ---------- stratum 2: field notes & trails ---------- */}
        {(w.trails.length > 0 || w.looseNotes.length > 0) && (
          <div className="stratum">
            <div className="stratum__rule"><span>field notes & trails</span><em>noticed ≠ believed · cheap · revisable</em></div>
            {w.trails.map(t => (
              <TrailCard key={t.id} t={t} ctx={ctx}
                drafted={drafted.has(t.id)}
                onDraft={() => setDrafted(x => new Set(x).add(t.id))} />
            ))}
            {w.looseNotes.length > 0 && (
              <div className="loose" id="el-loose">
                {w.looseNotes.map(n => (
                  <div key={n.id} className="lnote" id={`el-note-${n.id}`}>
                    <span className="lnote__ts">{n.ts}</span>
                    <span className={`lnote__who lnote__who--${n.origin}`}>{n.origin === 'guide' ? '✦ Guide' : 'you'}</span>
                    <span className="lnote__text">
                      {n.text}
                      {n.ref && (
                        <button className="ref ref--fig frag__ref" onClick={() => ctx.toggleDisclose(n.ref!)}>
                          {w.figureTitles[n.ref] ?? n.ref}
                        </button>
                      )}
                      {n.draft && <span className="draftb" title="drafted by the agent during a working session — not yet ratified">draft</span>}
                    </span>
                  </div>
                ))}
                {w.looseNotes.map(n => n.ref && disclosed.has(n.ref) && (
                  <FigureEmbed key={n.ref} figId={n.ref} ctx={ctx} />
                ))}
                <div className="loose__sweep">free-floating notes get a weekly file-or-fade sweep — attach to a trail, a question, or let them fade</div>
              </div>
            )}
          </div>
        )}

        {/* ---------- stratum 3: the work record ---------- */}
        {/* two grains over the same substance: BY RUN (flat chronology) or
            BY SESSION (episodes — each session a super-row with its runs
            nested; solo/automatic runs stand alone). The session is the
            chain; sometimes the chain is what you remember. */}
        <div className="stratum" id="el-sediment">
          <div className="stratum__rule">
            <span>sediment — the work record</span>
            <em>{grain === 'session'
              ? 'every session and run · complete · leftovers counted, nothing lost'
              : 'every run · one line each · nothing lost, nothing demands reading'}</em>
            {(w.sessions?.length ?? 0) > 0 && (
              <span className="sed-grain">
                {(['run', 'session'] as const).map(g => (
                  <button key={g} className={`sed-grain__btn ${grain === g ? 'is-on' : ''}`}
                          title={g === 'session' ? 'group the work by sitting — the session is the chain' : 'flat chronology of runs'}
                          onClick={() => setGrain(g)}>by {g}</button>
                ))}
              </span>
            )}
          </div>
          {w.sediment.length === 0 && (
            <div className="sed-empty">nothing has run yet — the first run writes the first line</div>
          )}
          <div className="sed-list">
            {grain === 'session' && (w.sessions?.length ?? 0) > 0 ? (
              <>
                {(w.sessions ?? []).map(s => {
                  const runs = w.sediment.filter(e => e.sessionRef === s.id)
                  return (
                    <div className="sedsess" key={s.id}>
                      <button className="sedsess__head" onClick={() => ctx.openSession(s.id)}
                              title="open the session — transcript, artifacts, leftovers">
                        <span className="sed__date">{s.when}</span>
                        <span className="sedsess__glyph">⟲</span>
                        <span className="sedsess__title">{s.label}</span>
                        <span className="sedsess__anchor">{s.anchor.label}</span>
                        <span className="sedsess__meta">
                          {s.turns} turns · {runs.length} run{runs.length === 1 ? '' : 's'} · {s.distillate.length} distilled
                        </span>
                        {s.leftovers.length > 0 && (
                          <span className="sedsess__left" title="produced but never pinned, noted, or discussed — kept findable">
                            {s.leftovers.length} unexamined
                          </span>
                        )}
                        <span className={`sedsess__state sedsess__state--${s.state}`}>{s.state}</span>
                      </button>
                      {runs.map(e => (
                        <SedimentRow key={e.id} e={e} ctx={ctx}
                          open={openSed.has(e.id)}
                          onToggle={() => setOpenSed(x => toggle(x, e.id))} />
                      ))}
                    </div>
                  )
                })}
                {w.sediment.filter(e => !e.sessionRef || !findSession(e.sessionRef)).length > 0 && (
                  <div className="sedsess sedsess--solo">
                    <div className="sedsess__head sedsess__head--solo">
                      <span className="sedsess__title">outside sessions</span>
                      <span className="sedsess__meta">automatic / solo runs — pipelines, scheduled QC, one-offs</span>
                    </div>
                    {w.sediment.filter(e => !e.sessionRef || !findSession(e.sessionRef)).map(e => (
                      <SedimentRow key={e.id} e={e} ctx={ctx}
                        open={openSed.has(e.id)}
                        onToggle={() => setOpenSed(x => toggle(x, e.id))} />
                    ))}
                  </div>
                )}
              </>
            ) : (
              w.sediment.map(e => (
                <SedimentRow key={e.id} e={e} ctx={ctx}
                  open={openSed.has(e.id)}
                  onToggle={() => setOpenSed(x => toggle(x, e.id))} />
              ))
            )}
          </div>
          <div className="sed-foot">
            append-only · chronological · the machine keeps this stratum; retention rides on each line
          </div>
        </div>

        <footer className="doc__foot">
          the notebook ages into the manuscript; the sediment ages into the supplement ·
          insight-rate ≈ narrative growth-rate, and both are rare — that is correct, not a failure
        </footer>
      </main>
      )}

      {/* ---------- right margin: bench / working session / docked transcript ---------- */}
      {/* keyed by anchor: each element's margin conversation is its own —
          switching targets must never carry the previous exchange along */}
      {showRight}
    </div>
  )
}
