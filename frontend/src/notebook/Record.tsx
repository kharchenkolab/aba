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

/** THE session marker (uniform everywhere): one arrow shape, state carried
 *  by fill/color — ▶ filled green = session live now, ▷ outline teal =
 *  session at rest (filed/parked). Runs' own ▶ state marks rhyme with it:
 *  the arrow family is the execution/session domain; fill is liveness. */
function SessGlyph({ live }: { live?: boolean }) {
  return <span className={`sessg ${live ? 'sessg--live' : ''}`}>{live ? '▶' : '▷'}</span>
}

const sessionLive = (w: World, id?: string) =>
  (w.sessions ?? []).some(s => (s.id === id || s.label === id) && s.state === 'open')

// ------------------------------------------------------------ pending state

/** Everything awaiting the user, DERIVED from record state (never a
 *  hand-kept counter — the tray and every count over it must agree by
 *  construction). Routine items are veto-tier: batchable, auto-filable
 *  with an undo window. Decisions (addenda, claim drafts) stay manual. */
export interface PendingItem {
  key: string
  kind: 'addendum' | 'fragment' | 'note' | 'claim draft' | 'plan' | 'proposal'
  label: string
  elId: string
  routine: boolean
  /** live mode: the proposals-store row id this item mirrors */
  liveId?: number
}
function derivePending(w: World): PendingItem[] {
  const out: PendingItem[] = []
  for (const s of w.sections) {
    for (const a of s.addenda) if (a.status === 'pending') {
      out.push({ key: a.id, kind: 'addendum', label: `${s.question} — addendum · ${a.on}`, elId: `el-${a.id}`, routine: false })
    }
    // a draft plan awaits "ratify the SHAPE" — one consent, spent up front;
    // filling it later happens at lowered ceremony
    if (s.planDraft && s.plan?.length) {
      out.push({ key: `${s.id}-plan`, kind: 'plan', label: `${s.question} — draft plan · ${s.plan.length} analyses — ratify the shape?`, elId: `el-plan-${s.id}`, routine: false })
    }
  }
  for (const t of w.trails) {
    t.fragments.forEach((f, i) => {
      if (f.draft) out.push({ key: `${t.id}:${i}`, kind: 'fragment', label: `${t.id} — “${f.text.slice(0, 56)}…”`, elId: `el-${t.id}`, routine: true })
    })
    if (t.nudge) out.push({ key: `${t.id}-nudge`, kind: 'claim draft', label: t.nudge.action, elId: `el-${t.id}`, routine: false })
  }
  for (const n of w.looseNotes) if (n.draft) {
    out.push({ key: n.id, kind: 'note', label: `note — “${n.text.slice(0, 56)}…”`, elId: `el-note-${n.id}`, routine: true })
  }
  // live mode: shared-store proposals ride the same tray — routing rows are
  // veto-tier (routine), everything else is a decision
  for (const p of w.liveTray ?? []) {
    out.push({ key: `live-${p.id}`, kind: 'proposal', liveId: p.id,
               label: `${p.kind} — ${p.headline}`,
               elId: p.sectionId ? `el-${p.sectionId}` : '',
               routine: p.kind === 'route' })
  }
  // spine face: pending rides on the question lines — the band count, the
  // tray rows, and the amber ticks are still ONE derivation
  for (const arc of w.spine?.arcs ?? []) {
    for (const q of arc.questions) for (const p of q.pending ?? []) {
      out.push({ key: p.key, kind: p.kind, label: `${q.title} — ${p.label}`, elId: `el-${q.id}`, routine: p.routine })
    }
  }
  return out
}

/** Peripheral deltas = scene-declared + DERIVED amber for every pending
 *  item's location (same state the tray shows — parity by construction). */
function effectiveDeltas(w: World, pending: PendingItem[]) {
  const declared = w.deltas ?? []
  const derived: NonNullable<World['deltas']> = []
  const seen = new Set(declared.map(d => d.elId))
  const byLoc = new Map<string, number>()
  for (const p of pending) {
    // amber lands on the TOC-navigable container (section/trail/loose)
    const loc = p.elId.startsWith('el-note-') ? 'el-loose'
      : p.kind === 'addendum' ? `el-${p.elId.replace(/^el-/, '').replace(/a\d+$/, '')}` : p.elId
    byLoc.set(loc, (byLoc.get(loc) ?? 0) + 1)
  }
  for (const [elId, count] of byLoc) {
    if (!seen.has(elId)) derived.push({ elId, kind: 'draft', count, label: `${count} awaiting you` })
  }
  return [...declared, ...derived]
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
  /** routine drafts are veto-tier: file in place, undoable */
  accepted: Set<string>
  accept: (key: string) => void
}

/** Render prose with [[kind:id|label]] live references. Block-level
 *  [[figure:id]] tokens are handled by splitBlocks() before this runs. */
function renderRefs(text: string, ctx: RefCtx): ReactNode[] {
  const { w } = ctx
  const out: ReactNode[] = []
  const re = /\[\[(fig|claim|run|trail|arc):([^\]|]+)(?:\|([^\]]+))?\]\]/g
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
    } else if (kind === 'arc') {
      const a = w.spine?.arcs.find(x => x.id === id)
      out.push(
        <button key={k++} className="ref ref--arc" title={a ? `${a.title} — jump to the arc` : id}
                onClick={() => ctx.scrollTo(`el-${id}`)}>
          {label ?? id}
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

function NarrativeSection({ s, ctx, methods, onMethods, onRatify, ratified, onWork, depth = 0 }: {
  s: Section; ctx: RefCtx
  methods: boolean; onMethods: () => void
  onRatify: (id: string) => void
  ratified: Set<string>
  onWork?: (sectionId: string) => void
  /** org depth — the Record is recursively hierarchical; a subquestion is
   *  a Section one level down, same organs, nested render */
  depth?: number
}) {
  const { w } = ctx
  const phaseNote = { early: 'early — mostly noticing', mid: 'mid — condensing', late: 'late — writing up' }[s.phase]
  const [govOpen, setGovOpen] = useState(false)
  const [pinned, setPinned] = useState(!!s.pinned)
  const [sessOpen, setSessOpen] = useState(false)
  // scale face: a dormant question is ONE quiet line — what it asks, what it
  // holds, since when — with a wake door; no dead scaffolding on screen
  if (s.dormant) {
    // a dormant node collapses its WHOLE subtree to this one line — depth
    // is the org axis, and parking acts on the node, children included
    return (
      <section className={`nsec nsec--dormant ${depth > 0 ? 'nsec--sub' : ''}`} id={`el-${s.id}`}>
        <span className="nsec__dq">{s.question}</span>
        {s.dormant.holds && <span className="nsec__dholds" title="the claim this question holds — live maturity">● {s.dormant.holds}</span>}
        {(s.children?.length ?? 0) > 0 && (
          <span className="nsec__dsub" title="subquestions folded under this dormant line — wake to expand">
            +{s.children!.length} subline{s.children!.length === 1 ? '' : 's'}
          </span>
        )}
        <span className="nsec__dsince">dormant since {s.dormant.since}</span>
        {w.work && (
          <button className="nsec__work" onClick={() => onWork?.(s.id)}
                  title="the play button, pointed at a sleeping question — a sitting opens with the question and its whole history in scope. Working never creates a thread: the question IS the thread; a sitting is a bounded episode on it"><SessGlyph /> wake</button>
        )}
      </section>
    )
  }
  // the live session's home locus wears a STANDING state — scroll away and
  // back, and where the work is landing stays unmistakable
  const anchored = w.anchorAt?.elId === s.id
  return (
    <section className={`nsec ${anchored ? 'nsec--live' : ''} ${depth > 0 ? 'nsec--sub' : ''}`} id={`el-${s.id}`}>
      {anchored && (
        <div className="nsec__livetag" title="this session's anchor — its products land here first; the state stands until the session closes">
          <SessGlyph live /> {w.anchorAt!.session} · working here
        </div>
      )}
      <div className="nsec__head">
        <h3>{s.question}</h3>
        {s.intent && (
          <span className="nsec__intent"
                title="a committed direction — marked by you, ahead of the evidence. The floor converts, never refuses: prose tracks evidence, structure tracks intent; growth along this line is pre-consented within the charge">
            committed direction · {s.intent.on}
          </span>
        )}
        <span className="nsec__phase" title="phase is per-question, derived from content — a young question in an old project is simply early">{phaseNote}</span>
        {s.charge && (
          <button className={`nsec__govtoggle ${govOpen ? 'is-on' : ''}`} onClick={() => setGovOpen(o => !o)}
                  title="the section's governing metadata — charge, budget, authorship, pin. Edited in place: the spine IS the map; there is no separate plan pane">
            § {govOpen ? '▾' : '▸'}
          </button>
        )}
        {s.paragraphs.length > 0 && (
          <button className={`nsec__methods ${methods ? 'is-on' : ''}`} onClick={onMethods}
                  title="expand every referenced result into its methods detail — generated from provenance, never hand-maintained">
            methods mode
          </button>
        )}
        {w.work && !anchored && (
          <button className="nsec__work" onClick={() => onWork?.(s.id)}
                  title="the play button, pointed at this question — a sitting opens with the question, its evidence, and its trails already in scope. Working never creates a thread: the question IS the thread; work adds bounded sittings to it. When a sitting is live here, this face becomes ▶ — you rejoin, never fork">
            <SessGlyph /> work
          </button>
        )}
      </div>
      {(s.claimsHeld?.length ?? 0) > 0 && (
        <div className="nsec__holds"
             title="what this question holds so far — claims at their current maturity; as prose lands and cites them, this strip retires">
          holds {renderRefs(s.claimsHeld!.map(id => `[[claim:${id}]]`).join(' · '), ctx)}
        </div>
      )}
      {govOpen && s.charge && (
        <div className="nsec__gov">
          <div className="nsec__govrow"><b>charge</b><span>{s.charge}</span></div>
          <div className="nsec__govrow"><b>phase</b><span>{phaseNote}</span></div>
          {s.budget && <div className="nsec__govrow"><b>budget</b><span>{s.budget}</span></div>}
          {s.authored && <div className="nsec__govrow"><b>authored</b><span>{s.authored}</span></div>}
          <div className="nsec__govrow"><b>state</b>
            <button className={`nsec__pin ${pinned ? 'is-pinned' : ''}`} onClick={() => setPinned(p => !p)}
                    title="the one-click veto — a pinned section is territory: agents may propose here, never act">
              {pinned ? '● pinned — agents propose, never act here' : '○ open — click to pin'}
            </button>
          </div>
        </div>
      )}
      {s.sessions && s.sessions.length > 0 && (
        <div className="nsec__sessions">
          {/* the story stratum READS; episode history is a door, not a list.
              Two rows at most sit inline — beyond that, one honest line. */}
          {(s.sessions.length > 2 && !sessOpen) ? (
            <button className="sess sess--sum" onClick={() => setSessOpen(true)}
                    title="the working episodes filed under this question — expand for the recent ones; the full history lives in the work record below">
              ▸ worked {(s.sessionsTotal ?? s.sessions.length)} times ·{' '}
              {s.sessions[0].when} – {s.sessions[s.sessions.length - 1].when}
            </button>
          ) : (
            <>
              {s.sessions.length > 2 && (
                <button className="sess sess--sum" onClick={() => setSessOpen(false)}
                        title="fold the episode list back to one line">
                  ▾ worked {(s.sessionsTotal ?? s.sessions.length)} times{
                    (s.sessionsTotal ?? 0) > s.sessions.length
                      ? ` · recent ${s.sessions.length}` : ''}
                </button>
              )}
              {s.sessions.map(x => (
                <button key={`${x.label}·${x.when}`} className="sess" onClick={() => ctx.openSession(x.label)}
                        title="the working exchange, filed under this question — the full episode: transcript, artifacts, leftovers; continuable">
                  <SessGlyph live={sessionLive(w, x.label)} /> {x.label} · {x.when} · {x.meta} — transcript
                </button>
              ))}
            </>
          )}
        </div>
      )}
      {s.paragraphs.length === 0 && (
        <div className="nsec__stub">
          <p>{s.plan?.length
            ? 'Nothing ratified yet — prose follows the evidence; the shape below is the plan.'
            : 'Nothing ratified yet — the story is written from evidence, not ahead of it.'}</p>
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
            {p.ratified.draftedBy ? `drafted by ${p.ratified.draftedBy} · ` : ''}
            {p.ratified.by && p.ratified.by !== '—'
              ? `ratified by ${p.ratified.by} · ` : 'ratified · '}{p.ratified.on}
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
      <PlanBlock s={s} w={w} onWork={onWork} />
      {(s.children?.length ?? 0) > 0 && (
        <div className="nsec__children">
          {s.children!.map(c => (
            <NarrativeSection key={c.id} s={c} ctx={ctx} methods={methods}
                              onMethods={onMethods} onRatify={onRatify}
                              ratified={ratified} onWork={onWork}
                              depth={depth + 1} />
          ))}
        </div>
      )}
    </section>
  )
}

// The manuscript's FUTURE TENSE: structure may run ahead of the evidence,
// prose may not — the skeleton wears its tense openly and collectively
// reads as the section's draft plan. The list is the scientist's most
// DIRECT control surface: adding, rewording, parking an item is the
// user's own intent and carries no ceremony (the propose→ratify gate
// exists for the agent's writes); every planned item launches (▷ work).
type PlanItem = NonNullable<Section['plan']>[number]
function PlanBlock({ s, w, onWork }: { s: Section; w: World; onWork?: (id: string) => void }) {
  const [items, setItems] = useState<PlanItem[]>(() => (s.plan ?? []).map(p => ({ ...p })))
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState<number | null>(null)
  const [editText, setEditText] = useState('')
  const [parked, setParked] = useState<{ item: PlanItem; at: number } | null>(null)

  if (items.length === 0 && !w.work) return null
  const done = items.filter(p => p.state === 'produced' || p.state === 'absorbed').length
  const mark = { planned: '○', 'taken-up': '▷', produced: '✓', absorbed: '↑' }
  const markTitle = {
    planned: 'planned — not yet taken up',
    'taken-up': 'taken up — a session is on it',
    produced: 'produced — the run landed; evidence in hand',
    absorbed: 'absorbed — its evidence now lives in the prose',
  }
  const add = () => {
    const text = draft.trim()
    if (!text) return
    setItems(xs => [...xs, { text, state: 'planned', mine: true }])
    setDraft('')
  }
  const commitEdit = (i: number) => {
    const text = editText.trim()
    setItems(xs => text ? xs.map((x, k) => k === i ? { ...x, text, mine: true } : x) : xs)
    setEditing(null)
  }
  // the plan block renders even when empty (a quiet seed composer) so any
  // question can start accruing planned work in place
  return (
    <div className={`plan ${items.length === 0 ? 'plan--seed' : ''}`} id={`el-plan-${s.id}`}
         title="planned work lives IN the section it would feed — the to-do list is distributed through the manuscript, not pooled in a backlog. The list is yours: add, reword, park — no ceremony; the agent proposes, you own the plan">
      {items.length > 0 && (
        <div className="plan__head">
          <span>{s.planDraft
            ? 'draft plan — proposed by Guide · ratify the shape, not prose'
            : 'the plan — structure ahead of the evidence, honestly future-tense'}</span>
          <span className="plan__gap"
                title="the gap between declared importance and present evidence is an honest, visible state — never thin prose pretending otherwise">
            evidence {done} of {items.length} planned analyses
          </span>
        </div>
      )}
      {items.map((p, i) => (
        <div key={i} className={`plan__item plan__item--${p.state}`}>
          <span className="plan__mark" title={markTitle[p.state]}>{mark[p.state]}</span>
          {editing === i ? (
            <input className="plan__edit" autoFocus value={editText}
                   onChange={e => setEditText(e.target.value)}
                   onKeyDown={e => { if (e.key === 'Enter') commitEdit(i); if (e.key === 'Escape') setEditing(null) }}
                   onBlur={() => commitEdit(i)} />
          ) : p.state === 'planned' ? (
            <button className="plan__text plan__text--editable" onClick={() => { setEditing(i); setEditText(p.text) }}
                    title="your plan — click to reword it, no ceremony">
              {p.text}
            </button>
          ) : (
            <span className="plan__text">{p.text}</span>
          )}
          {p.mine && <span className="plan__meta">yours</span>}
          {p.meta && <span className="plan__meta">{p.meta}</span>}
          {p.state === 'planned' && (
            <span className="plan__acts">
              {w.work && (
                <button className="nsec__work" onClick={() => onWork?.(s.id)}
                        title="the play button, pointed at this planned line — a sitting opens scoped by the stub: its charge, its intent, the evidence so far, and this item; it rides the question's thread. Ask for a fuller technical plan first if you want one; it returns through the same ratification gate">
                  <SessGlyph /> work
                </button>
              )}
              <button className="plan__x" title="park it — off the plan, remembered, one-click undo"
                      onClick={() => { setParked({ item: p, at: i }); setItems(xs => xs.filter((_, k) => k !== i)) }}>
                ✕
              </button>
            </span>
          )}
        </div>
      ))}
      {parked && (
        <button className="plan__undo"
                onClick={() => { setItems(xs => { const n = [...xs]; n.splice(Math.min(parked.at, n.length), 0, parked.item); return n }); setParked(null) }}>
          parked “{parked.item.text.slice(0, 44)}…” — undo
        </button>
      )}
      {w.work && (
        <div className="plan__composer">
          <input placeholder={items.length === 0 ? '+ plan an analysis for this question…' : '+ add a planned analysis…'}
                 value={draft} onChange={e => setDraft(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') add() }}
                 title="lands as ○ planned, yours immediately — your own plan needs no ratification" />
          {draft.trim() && <button className="btn" onClick={add}>add</button>}
        </div>
      )}
    </div>
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
  const [unfolded, setUnfolded] = useState(false)
  // scale face: stalled trails fold to one line — findable, not loud
  if (t.state === 'stalled' && !unfolded) {
    return (
      <button className="trail trail--folded" id={`el-${t.id}`} onClick={() => setUnfolded(true)}
              title="stalled — no fragment in a while; folded, never lost">
        <span className="trail__id">{t.id}</span>
        <span className="trail__foldtitle">{t.title}</span>
        <span className="trail__foldmeta">{t.fragments.length} fragments · last {t.fragments[t.fragments.length - 1]?.ts} · stalled — unfold ▾</span>
      </button>
    )
  }
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
              {f.draft && (ctx.accepted.has(`${t.id}:${i}`)
                ? <span className="draftb draftb--done">✓ filed</span>
                : <button className="draftb draftb--act" onClick={() => ctx.accept(`${t.id}:${i}`)}
                          title="routine drafts file in place — one click, undoable from the tray">draft — file ✓</button>)}
              {f.src && (
                <button className="frag__turn" onClick={() => ctx.openSession(f.src!.sess, f.src!.turn)}
                        title="provenance for prose: the exchange that drafted this fragment">
                  <SessGlyph live={sessionLive(ctx.w, f.src.sess)} /> turn {f.src.turn}
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
            <SessGlyph live={sessionLive(ctx.w, e.sessionRef)} /> {e.sessionRef}
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

// --------------------------------------------------------------------- spine
// The project-grain face (the Record recurses — see world.ts). Every
// question is ONE line whose face follows its state; arcs collapse whole;
// the full detail face lives one level down, behind `open ▸`.

function SpineQRow({ q, ctx, onAdvance, badge }: {
  q: import('./world').SpineQ; ctx: RefCtx
  onAdvance?: (t: string) => void; badge?: ReactNode
}) {
  if (q.state === 'dead') {
    return (
      <div className="spq spq--dead" id={`el-${q.id}`}
           title="the epitaph line: hypothesis · verdict · the run that killed it. The paper reports the survivors; the record keeps the casualties — searchable forever (“did we ever try…?”)">
        <span className="spq__glyph spq__glyph--dead">†</span>
        <span className="spq__title">{q.title}</span>
        <span className="spq__verdict">{q.epitaph?.verdict}</span>
        <span className="spq__run">{q.epitaph?.run}</span>
        <span className="spq__date">{q.epitaph?.date}</span>
      </div>
    )
  }
  if (q.state === 'held') {
    return (
      <div className="spq spq--held" id={`el-${q.id}`}>
        <span className="spq__glyph">◦</span>
        <span className="spq__title">{q.title}</span>
        {q.holds && <span className="spq__holds" title="the claim this line holds while it sleeps — live maturity">● {q.holds}</span>}
        <span className="spq__date">held since {q.since}</span>
        <button className="nsec__work" onClick={() => onAdvance?.(`wake:${q.id}`)}
                title="the play button, pointed at a sleeping question — a sitting opens with its whole history in scope; the question's thread simply continues">wake ▸</button>
        {badge}
      </div>
    )
  }
  if (q.state === 'closed') {
    return (
      <div className="spq spq--closed" id={`el-${q.id}`}>
        <span className="spq__glyph spq__glyph--ok">✓</span>
        <span className="spq__title">{q.title}</span>
        <span className="spq__holds">{q.holds}</span>
        <button className="spq__page" onClick={() => onAdvance?.(`descend:${q.id}`)}
                title="the full question page — narrative, trails, sediment slice, sessions — one level down">page ▸</button>
      </div>
    )
  }
  return (
    <div className="spq spq--openq" id={`el-${q.id}`}>
      <div className="spq__line1">
        {q.session
          ? <button className="spq__sess" onClick={() => ctx.openSession(q.session!.label)}
                    title={q.session.live ? 'session live on this question now' : 'last session on this question — transcript'}>
              <SessGlyph live={q.session.live} /> {q.session.label}
            </button>
          : <span className="spq__glyph">·</span>}
        <span className="spq__title spq__title--open">{q.title}</span>
        {badge}
        {q.activity && <span className="spq__act">{q.activity}</span>}
        <button className="spq__page spq__page--primary" onClick={() => onAdvance?.(`descend:${q.id}`)}
                title="descend — the full page (today's whole notebook face) lives at question grain">open ▸</button>
      </div>
      {q.now && <div className="spq__now">{q.now}</div>}
    </div>
  )
}

function SpineArcBlock({ arc, ctx, onAdvance, badgeFor, rollup }: {
  arc: import('./world').SpineArc; ctx: RefCtx
  onAdvance?: (t: string) => void
  badgeFor: (elId: string) => ReactNode
  rollup: ReactNode
}) {
  const [open, setOpen] = useState(!!arc.open)
  const n = (st: string) => arc.questions.filter(q => q.state === st).length
  const counts = [
    n('closed') ? `${n('closed')} closed` : '',
    n('open') ? `${n('open')} open` : '',
    n('held') ? `${n('held')} held` : '',
    n('dead') ? `${n('dead')} ruled out` : '',
  ].filter(Boolean).join(' · ')
  return (
    <section className={`arc ${open ? '' : 'arc--folded'}`} id={`el-${arc.id}`}>
      <button className="arc__head" onClick={() => setOpen(o => !o)}
              title={open ? 'fold the arc' : 'unfold — every question one line'}>
        <span className="arc__id">{arc.id}</span>
        <span className="arc__title">{arc.title}</span>
        <span className="arc__era">{arc.era}</span>
        <span className="arc__counts">{counts}{arc.runs ? ` · ${arc.runs} runs` : ''}</span>
        {/* roll-up only when folded — open arcs show badges on the rows themselves */}
        {!open && rollup}
        <span className="arc__disc">{open ? '▾' : '▸'}</span>
      </button>
      {/* the folded arc's ABSTRACT face: not just counts — what it holds */}
      {!open && arc.holds && (
        <div className="arc__holds" title="the chapter's claim, held while it sleeps — the fold is an abstract, not a blank">● {arc.holds}</div>
      )}
      {open && (
        <div className="arc__qs">
          {arc.questions.map(q => (
            <SpineQRow key={q.id} q={q} ctx={ctx} onAdvance={onAdvance} badge={badgeFor(`el-${q.id}`)} />
          ))}
        </div>
      )}
    </section>
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

// ------------------------------------------------------------------ the tree

/** Depth-annotated walk of the section tree — the org axis is recursive,
 *  and every flat consumer (TOC, anchors, search) must see every node. */
function walkSections(list: Section[], depth = 0): { s: Section; depth: number }[] {
  return list.flatMap(s => [{ s, depth }, ...walkSections(s.children ?? [], depth + 1)])
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
    for (const { s } of walkSections(w.sections)) {
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
  // the spine: closed claims and open now-lines search as story; EPITAPHS
  // get their own stratum — "did we ever try X?" must answer in one query,
  // years later, with the run that killed it
  if (w.spine && (scope === 'story' || scope === 'everything')) {
    for (const arc of w.spine.arcs) for (const q of arc.questions) {
      if (q.state === 'dead') {
        if (has(q.title) || has(q.epitaph?.verdict ?? ''))
          hits.push({ domId: `el-${q.id}`, label: `† ${q.title} — ${q.epitaph?.verdict ?? 'ruled out'} (${q.epitaph?.run ?? ''})`, stratum: 'epitaph' })
      } else if (has(q.title) || has(q.holds ?? '') || has(q.now ?? '')) {
        hits.push({ domId: `el-${q.id}`, label: `${arc.id} · ${q.title}`, stratum: 'story' })
      }
    }
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

/** The TRIAGE BAND — one glance answers the whole visit: anything broken
 *  (⚡ conditions)? anything needs me (▢ → the tray)? anything running (▶)?
 *  where was I (resume ▷/▶, held ⌖)? Each slot is a DOOR; empty slots
 *  don't render. Glyph grammar: the ARROW marks a door to a session,
 *  fill/color carries state — ▷ outline teal = at rest, ▶ filled green =
 *  live now (rhymes with runs' own ▶ state marks). */
function TriageBand({ w, pending, deltas, held, trayOpen, onToggleTray, onOpenSession, onJump, children }: {
  w: World
  pending: PendingItem[]
  deltas: NonNullable<World['deltas']>
  held: { elId: string; label: string }[]
  trayOpen: boolean
  onToggleTray: () => void
  onOpenSession: (id: string) => void
  onJump: (elId: string) => void
  children?: ReactNode   // the tray, rendered by the parent (owns the state)
}) {
  const d = w.desk!
  const conditions = deltas.filter(x => x.kind === 'condition')
  const running = w.sediment.filter(e => e.state === 'running')
  return (
    <div className="desk" title="the triage band — anything broken? anything needs me? anything running? where was I?">
      <span className="desk__kicker">now</span>
      {conditions.length > 0 && (
        <button className="desk__item desk__cond" onClick={() => onJump(conditions[0].elId)}
                title={conditions.map(c => c.label).join(' · ')}>
          ⚡ {conditions.length === 1 ? conditions[0].label.split(' — ')[0] : `${conditions.length} conditions`}
        </button>
      )}
      {pending.length > 0 && (
        <button className={`desk__item desk__needs ${trayOpen ? 'is-open' : ''}`} onClick={onToggleTray}
                title="everything awaiting you, in one tray — ratify, file, or defer without hunting">
          ▢ {pending.length} need{pending.length === 1 ? 's' : ''} you {trayOpen ? '▴' : '▾'}
        </button>
      )}
      {running.length > 0 && (
        <button className="desk__item is-live" onClick={() => onJump(`el-${running[0].id}`)}
                title={running.map(r => r.title).join(' · ')}>
          ▶ {running.length === 1 ? running[0].title : `${running.length} running`}
        </button>
      )}
      {d.items.map(i => (
        <button key={i.label} className={`desk__item ${i.live ? 'is-live' : ''}`}
                onClick={() => { if (i.sessionId) onOpenSession(i.sessionId) }}>
          {i.sessionId ? <><SessGlyph live={i.live} />{' '}</> : null}{i.label}
          <span className="desk__meta"> · {i.meta}</span>
        </button>
      ))}
      {held.map(h => (
        <button key={h.elId} className="desk__item desk__item--held" onClick={() => onJump(h.elId)}
                title="held for this session — click to jump back; clears at session close">
          ⌖ {h.label}
        </button>
      ))}
      {children}
    </div>
  )
}

/** The delta rail — a minimap of change. Ticks sit at each changed
 *  element's position in the document; three tiers only. Click = jump.
 *  The rail may glow; the page never scrolls itself. */
function DeltaRail({ deltas, seen, docRef, onJump }: {
  deltas: NonNullable<World['deltas']>
  seen: Set<string>
  docRef: React.RefObject<HTMLElement | null>
  onJump: (elId: string) => void
}) {
  const [ticks, setTicks] = useState<{ pct: number; kind: string; elId: string; label: string }[]>([])
  useEffect(() => {
    const doc = docRef.current
    if (!doc || !deltas.length) { setTicks([]); return }
    const t = setTimeout(() => {
      const total = doc.scrollHeight
      setTicks(deltas.map(d => {
        const el = document.getElementById(d.elId)
        if (!el) return null
        const top = el.getBoundingClientRect().top - doc.getBoundingClientRect().top + doc.scrollTop
        return { pct: Math.min(97, (top / total) * 100), kind: d.kind, elId: d.elId, label: d.label }
      }).filter(Boolean) as { pct: number; kind: string; elId: string; label: string }[])
    }, 120)   // after layout (images have known aspect from CSS; 120ms suffices for the mock)
    return () => clearTimeout(t)
  }, [deltas, docRef])
  if (!ticks.length) return null
  return (
    <div className="drail-wrap">
      <div className="drail" title="the delta rail — where change landed, by kind; click a tick to jump">
        {ticks.map(t => (
          <button key={t.elId + t.kind}
                  className={`drail__tick drail__tick--${t.kind} ${t.kind === 'accretion' && seen.has(t.elId) ? 'is-seen' : ''}`}
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

export default function Record(props: { world?: World; onAdvance?: (t: string) => void
                                        triage?: { accept(id: number): Promise<void>
                                                   dismiss(id: number): Promise<void>
                                                   undo(id: number): Promise<void> } }) {
  const w = props.world ?? coastalWorld
  if (w.bare) return <BareStart w={w} onAdvance={props.onAdvance} />
  return <RecordDoc w={w} onAdvance={props.onAdvance} triage={props.triage} />
}

type TriageApi = { accept(id: number): Promise<void>
                   dismiss(id: number): Promise<void>
                   undo(id: number): Promise<void> }

function RecordDoc({ w, onAdvance, triage }: { w: World; onAdvance?: (t: string) => void
                                               triage?: TriageApi }) {
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
  const [accepted, setAccepted] = useState<Set<string>>(new Set())
  const [trayOpen, setTrayOpen] = useState(() => new URLSearchParams(window.location.search).get('tray') === '1')
  const [undoable, setUndoable] = useState<{ keys: string[]; label: string
                                             revert?: () => void } | null>(null)
  const [seenAcc, setSeenAcc] = useState<Set<string>>(new Set())
  const [omni, setOmni] = useState<{ open: boolean; q: string; asked: boolean }>({ open: false, q: '', asked: false })
  const [prevSyn, setPrevSyn] = useState(false)
  const [view, setView] = useState<'record' | 'onepager' | 'digest'>(
    () => new URLSearchParams(window.location.search).get('view') === 'onepager' && w.onePager ? 'onepager' : 'record')
  const [rfcState, setRfcState] = useState<'open' | 'accepted' | 'partial' | 'deferred' | 'never'>('open')
  const [ratchetState, setRatchetState] = useState<'open' | 'yes' | 'keep'>('open')
  const [newOpen, setNewOpen] = useState(false)
  const [q, setQ] = useState('')
  const [scope, setScope] = useState<'story' | 'noticed' | 'everything'>('everything')
  const [activeAnchor, setActiveAnchor] = useState('')

  // everything awaiting the user, derived from record state — the tray and
  // every count over it agree by construction (minus what was acted on here)
  const pending = useMemo(
    () => derivePending(w).filter(p =>
      !ratified.has(p.key) && !accepted.has(p.key) &&
      !(p.kind === 'claim draft' && drafted.has(p.key.replace('-nudge', '')))),
    [w, ratified, accepted, drafted])
  const deltas = useMemo(() => effectiveDeltas(w, pending), [w, pending])

  // ⌘K — the fast entry: ask or find, from anywhere
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOmni(o => ({ open: !o.open, q: '', asked: false }))
      }
      if (e.key === 'Escape') setOmni(o => o.open ? { ...o, open: false } : o)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // TOC tracks the reader's position: the anchor nearest above the upper
  // third of the document column is "where you are".
  const ANCHORS = w.spine
    ? [...w.spine.arcs.map(a => `el-${a.id}`), 'el-sediment']
    : [
        ...walkSections(w.sections).map(({ s }) => `el-${s.id}`),
        ...w.trails.map(t => `el-${t.id}`),
        ...(w.looseNotes.length ? ['el-loose'] : []),
        'el-sediment',
      ]
  const onDocScroll = (e: React.UIEvent<HTMLElement>) => {
    const doc = e.currentTarget
    const rect = doc.getBoundingClientRect()
    const line = rect.top + doc.clientHeight / 3
    let best = ''
    for (const id of ANCHORS) {
      const el = document.getElementById(id)
      if (el && el.getBoundingClientRect().top <= line) best = id
    }
    if (best !== activeAnchor) setActiveAnchor(best)
    // lifecycle: ACCRETION signals clear once their region has been seen —
    // routine ticks must never become wallpaper (amber/red persist)
    for (const d of deltas) {
      if (d.kind !== 'accretion' || seenAcc.has(d.elId)) continue
      const el = document.getElementById(d.elId)
      if (!el) continue
      const r = el.getBoundingClientRect()
      if (r.top < rect.bottom && r.bottom > rect.top) {
        setSeenAcc(s => new Set(s).add(d.elId))
      }
    }
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
    accepted,
    accept: key => { setAccepted(s => new Set(s).add(key)); setUndoable({ keys: [key], label: 'filed 1 item' }) },
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

  // ------------------------------------------------ weekly digest
  // the busy PI's consumption format for projects they don't open daily:
  // conditions · needs-you · what's new · one figure — auto-rendered
  if (view === 'digest' && w.whatsNew) {
    // conditions come from BOTH loud news and standing condition deltas —
    // a condition that made no news this week is still a condition
    const loud = [
      ...w.whatsNew.items.filter(i => i.loud).map(i => i.text),
      ...deltas.filter(d => d.kind === 'condition' && !w.whatsNew!.items.some(i => i.loud && i.elId === d.elId)).map(d => d.label),
    ]
    const news = w.whatsNew.items.filter(i => !i.loud)
    return (
      <div className="rec rec--onepager">
        <main className="doc doc--onepager">
          <div className="doc__viewbar">
            <button className="btn" onClick={() => setView('record')}>← full record</button>
            <span className="doc__viewnote">auto-rendered from the record · emailable · the format for the four projects you don't open daily</span>
          </div>
          <h1>{w.project.title} — this week</h1>
          <div className="op__sig">since {w.whatsNew.since} · {w.whatsNew.items.length} events · {pending.length} awaiting you</div>
          {loud.length > 0 && (
            <section className="dg__block dg__block--cond">
              <h2>⚡ conditions</h2>
              {loud.map((t, k) => <p key={k}>{t}</p>)}
            </section>
          )}
          {pending.length > 0 && (
            <section className="dg__block dg__block--needs">
              <h2>▢ needs you · {pending.length}</h2>
              {pending.map(p => <p key={p.key}>{p.label}</p>)}
            </section>
          )}
          {news.length > 0 && (
            <section className="dg__block">
              <h2>new this week</h2>
              {news.map((i, k) => <p key={k}><span className="wnew__ts">{i.ts}</span>{i.text}</p>)}
            </section>
          )}
          {w.digestFig && (
            <figure className="fig">
              <img src={ART(w.digestFig)} alt="figure of the week" />
              <figcaption><span>figure of the week — {w.figureTitles[w.digestFig] ?? w.digestFig}</span></figcaption>
            </figure>
          )}
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
  // peripheral change signals on the TOC: pulse badges, three tiers;
  // accretion fades once seen (lifecycle), amber/red persist until resolved
  const deltaBadge = (elId: string) => {
    const d = deltas.find(x => x.elId === elId)
    if (!d) return null
    const seen = d.kind === 'accretion' && seenAcc.has(d.elId)
    return (
      <span className={`toc__delta toc__delta--${d.kind} ${seen ? 'is-seen' : ''}`} title={d.label}>
        {d.kind === 'condition' ? '⚡' : `+${d.count ?? 1}`}
      </span>
    )
  }
  // spine periphery ROLLS UP the tree: a badge on an arc means something
  // inside it changed — same three tiers, counts aggregating upward
  const arcRollup = (a: import('./world').SpineArc) => {
    const ids = new Set(a.questions.map(q => `el-${q.id}`))
    const rel = deltas.filter(d => ids.has(d.elId))
    if (!rel.length) return null
    const cond = rel.some(d => d.kind === 'condition')
    const draft = rel.filter(d => d.kind === 'draft').reduce((x, d) => x + (d.count ?? 1), 0)
    const acc = rel.filter(d => d.kind === 'accretion' && !seenAcc.has(d.elId)).reduce((x, d) => x + (d.count ?? 1), 0)
    return (
      <span className="toc__rollup" title={rel.map(d => d.label).join(' · ')}>
        {cond && <span className="toc__delta toc__delta--condition">⚡</span>}
        {draft > 0 && <span className="toc__delta toc__delta--draft">+{draft}</span>}
        {acc > 0 && <span className="toc__delta toc__delta--accretion">+{acc}</span>}
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
        <div className="toc__project">{w.crumb ? w.crumb.up : w.project.title}</div>
        <div className="toc__since">since {w.project.started.slice(0, 7)}</div>

        {w.spine ? (
          <>
            <div className="toc__group">the spine</div>
            {w.spine.arcs.map(a => (
              <button key={a.id} className={`toc__item ${activeAnchor === `el-${a.id}` ? 'is-active' : ''}`} onClick={() => scrollTo(`el-${a.id}`)}>
                <span className="toc__arcid">{a.id}</span> {a.title}
                {arcRollup(a)}
              </button>
            ))}
          </>
        ) : (
          <>
            {w.sections.length > 0 && <div className="toc__group">story so far</div>}
            {walkSections(w.sections).map(({ s, depth }) => (
              <button key={s.id} className={`toc__item ${depth > 0 ? 'toc__item--sub' : ''} ${activeAnchor === `el-${s.id}` ? 'is-active' : ''}`} onClick={() => scrollTo(`el-${s.id}`)}>
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
          </>
        )}
        <div className="toc__group">sediment</div>
        <button className={`toc__item ${activeAnchor === 'el-sediment' ? 'is-active' : ''}`} onClick={() => scrollTo('el-sediment')}>
          {(w.sedimentTotal ?? w.sediment.length).toLocaleString()} run{(w.sedimentTotal ?? w.sediment.length) === 1 ? '' : 's'}{w.spine ? ` · ${w.spine.sessionsTotal} sessions` : ''} · complete · automatic
          {deltaBadge('el-sediment')}
        </button>

        <div className="toc__spacer" />
        <button className="toc__omni" onClick={() => setOmni({ open: true, q: '', asked: false })}
                title="ask or find, from anywhere — typing is the fastest move (⌘K)">
          ⌘K · ask or find
        </button>
        {w.whatsNew && (
          <button className="toc__onepager" onClick={() => setView('digest')}
                  title="this week in the project — conditions, decisions, new results; auto-rendered, emailable">
            this week ▸
          </button>
        )}
        {w.onePager && (
          <button className="toc__onepager" onClick={() => setView('onepager')}
                  title={w.spine
                    ? 'the paper being assembled from the record — the one-pager grown up; same machinery, no separate writing surface'
                    : 'the same record rendered for the one-number visitor (§ focus spectrum)'}>
            {w.spine ? 'the manuscript seed ▸' : 'view as one-pager'}
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
        <DeltaRail deltas={deltas} seen={seenAcc} docRef={docRef} onJump={elId => scrollTo(elId)} />
        <header className="doc__head">
          {w.crumb && (
            <button className="doc__crumb" onClick={() => onAdvance?.('ascend')}
                    title="back up to the project spine — this page is one question's Record, one level down">
              ‹ {w.crumb.up} <span className="doc__crumbarc">· {w.crumb.arc}</span>
            </button>
          )}
          <h1>{w.project.title}</h1>
          <div className="doc__sub">
            {w.spine
              ? 'the project spine — a rolling synthesis over arcs · every question one line · detail lives one level down'
              : w.crumb
                ? 'a question page — the full Record face, at question grain · co-written, ratified by you'
                : 'a record co-written by you and Guide · narrative is yours to ratify · sediment keeps itself'}
          </div>
        </header>

        {/* the triage band: conditions · needs-you (tray) · running · resume · held */}
        {w.desk && (
          <TriageBand w={w} pending={pending} deltas={deltas} held={held}
                      trayOpen={trayOpen} onToggleTray={() => setTrayOpen(o => !o)}
                      onOpenSession={id => ctx.openSession(id)} onJump={elId => { setTrayOpen(false); scrollTo(elId) }}>
            {trayOpen && (
              <div className="tray" title="everything awaiting you — derived from the record itself, so this list and the count always agree">
                <div className="tray__head">
                  <span>needs you · {pending.length}</span>
                  {pending.some(p => p.routine) && (
                    <button className="btn"
                            title="routine items are veto-tier: batch-file them; decisions (addenda, claim drafts) stay one-by-one"
                            onClick={() => {
                              const keys = pending.filter(p => p.routine).map(p => p.key)
                              setAccepted(s => new Set([...s, ...keys]))
                              setUndoable({ keys, label: `filed ${keys.length} routine` })
                            }}>
                      file all routine ({pending.filter(p => p.routine).length})
                    </button>
                  )}
                  {undoable && (
                    <button className="tray__undo"
                            onClick={() => {
                              undoable.revert?.()
                              setAccepted(s => { const n = new Set(s); undoable.keys.forEach(k => n.delete(k)); return n })
                              setRatified(s => { const n = new Set(s); undoable.keys.forEach(k => n.delete(k)); return n })
                              setUndoable(null)
                            }}>
                      ✓ {undoable.label} — undo
                    </button>
                  )}
                </div>
                {pending.length === 0 && <div className="tray__empty">nothing needs you — the record is current</div>}
                {pending.map(p => (
                  <div key={p.key} className="tray__row">
                    <span className={`tray__kind tray__kind--${p.routine ? 'routine' : 'decision'}`}>{p.kind}</span>
                    <span className="tray__label">{p.label}</span>
                    {(p.kind === 'addendum' || p.kind === 'plan') && (
                      <button className="btn btn--primary"
                              title={p.kind === 'plan' ? 'ratify the SHAPE — one consent, spent up front; filling it happens at lowered ceremony' : undefined}
                              onClick={() => { setRatified(s => new Set(s).add(p.key)); setUndoable({ keys: [p.key], label: 'ratified 1' }) }}>
                        Ratify
                      </button>
                    )}
                    {p.routine && p.kind !== 'proposal' && (
                      <button className="btn"
                              onClick={() => { setAccepted(s => new Set(s).add(p.key)); setUndoable({ keys: [p.key], label: 'filed 1' }) }}>
                        file ✓
                      </button>
                    )}
                    {p.kind === 'claim draft' && (
                      <button className="btn"
                              title="later is a place, not a pile — this lands as a planned item in the section it belongs to; no ceremony, it is your plan, and it waits there with its own ▷ work launcher"
                              onClick={() => { setAccepted(s => new Set(s).add(p.key)); setUndoable({ keys: [p.key], label: 'planned 1 — filed as a planned item under its question' }) }}>
                        → plan
                      </button>
                    )}
                    {/* live proposals: the SAME row the classic UI triages —
                        accept fires the handler server-side; undo is real */}
                    {p.kind === 'proposal' && p.liveId != null && triage && (
                      <>
                        <button className="btn btn--primary"
                                onClick={() => {
                                  const id = p.liveId!
                                  triage.accept(id).then(() => {
                                    setAccepted(s => new Set(s).add(p.key))
                                    setUndoable({ keys: [p.key], label: 'accepted 1',
                                                  revert: () => { triage.undo(id).catch(() => {}) } })
                                  }).catch(() => setUndoable({ keys: [], label: '⚠ accept failed — still pending' }))
                                }}>
                          accept ✓
                        </button>
                        <button className="btn"
                                onClick={() => {
                                  const id = p.liveId!
                                  triage.dismiss(id).then(() => {
                                    setAccepted(s => new Set(s).add(p.key))
                                    setUndoable({ keys: [p.key], label: 'dismissed 1 — it will not re-nag until the world changes',
                                                  revert: () => { triage.undo(id).catch(() => {}) } })
                                  }).catch(() => setUndoable({ keys: [], label: '⚠ dismiss failed — still pending' }))
                                }}>
                          dismiss
                        </button>
                      </>
                    )}
                    <button className="btn" onClick={() => { setTrayOpen(false); scrollTo(p.elId) }}
                            title="see it in context before deciding">go →</button>
                  </div>
                ))}
                {/* the trust ratchet, downward: ceremony is earned away —
                    visibly, reversibly; it rises again on rejection */}
                {w.ratchet && ratchetState === 'open' && (
                  <div className="tray__ratchet">
                    <span>⟡ {w.ratchet.text}</span>
                    <button className="btn" onClick={() => setRatchetState('yes')}>yes, fold them</button>
                    <button className="btn" onClick={() => setRatchetState('keep')}>keep showing me</button>
                  </div>
                )}
                {w.ratchet && ratchetState === 'yes' && (
                  <div className="tray__ratchet tray__ratchet--done">
                    done — number refreshes land in the briefing and the ledger only; reversible in governance
                  </div>
                )}
                <div className="tray__foot"
                     title="fade ≠ accept: nothing is auto-ratified — faded drafts stay findable in their strata; only the claim on your attention is released. Decisions never fade; they fold into the digest instead of accreting badge weight">
                  decide here or in place — same state either way · dismissals are remembered ·
                  unattended routine drafts fade after the sweep (findable, never nagging); decisions wait
                </div>
              </div>
            )}
          </TriageBand>
        )}

        {/* re-entry past a few days: a BRIEFING, not a diff — authored,
            past tense, ranked by consequence, scaled to time away */}
        {w.briefing && (
          <section className="brief"
                   title="a colleague's twenty seconds, not a changelog: what changed in the science, in the order a colleague would say it — and what the system could not resolve">
            <div className="brief__head">
              since you last read this — {w.briefing.away}
              <em>a briefing, not a diff · ranked by consequence</em>
            </div>
            {w.briefing.paras.map((p, i) => p.elId ? (
              <button key={i} className="brief__para brief__para--door" onClick={() => scrollTo(p.elId!)} title="jump to it">
                {p.text}<span className="wnew__go"> →</span>
              </button>
            ) : (
              <p key={i} className="brief__para">{p.text}</p>
            ))}
            {w.briefing.flag && (
              <button className="brief__flag" onClick={() => w.briefing!.flag!.elId && scrollTo(w.briefing!.flag!.elId)}
                      title="the cheapest trust-building move a system can make: flagging its own inability to resolve something">
                ⚠ {w.briefing.flag.text}
              </button>
            )}
            <div className="brief__held"
                 title="the absence policy: the fast clock ran (numbers, figures, sediment stayed current); nothing structural applied; class-2 expiry timers PAUSED while you were away">
              {w.briefing.held}
            </div>
          </section>
        )}

        {/* what's new */}
        {w.whatsNew && (
          <section className={`wnew ${newOpen ? 'is-open' : ''}`}>
            <button className="wnew__head" onClick={() => setNewOpen(o => !o)}>
              <span className="wnew__count">what's new{w.whatsNew.since ? ` since ${w.whatsNew.since}` : ''} · {w.whatsNew.items.length}</span>
              <span className="wnew__peek">
                {w.whatsNew.items.filter(i => i.loud || i.live).map((i, k) => (
                  <span key={k} className={`wnew__chip ${i.loud ? 'is-loud' : ''} ${i.live ? 'is-live' : ''}`}>
                    {i.live ? '▶ ' : i.loud ? '⚡ ' : ''}
                    {i.loud ? i.text.split(' (')[0] : i.text.split(' — ')[0]}
                  </span>
                ))}
              </span>
              {!w.desk && <span className="wnew__behind" title="degradation is visible and recoverable, never silent rot">{behindLine}</span>}
              <span>{newOpen ? '▾' : '▸'}</span>
            </button>
            {newOpen && (
              <div className="wnew__body">
                {/* every item is a DOOR: the strip names work AND takes you there */}
                {w.whatsNew.items.map((i, k) => i.elId ? (
                  <button key={k} className={`wnew__item wnew__item--door ${i.loud ? 'is-loud' : ''}`}
                          onClick={() => scrollTo(i.elId!)} title="jump to it">
                    <span className="wnew__ts">{i.ts}</span>{i.text}<span className="wnew__go"> →</span>
                  </button>
                ) : (
                  <div key={k} className={`wnew__item ${i.loud ? 'is-loud' : ''}`}>
                    <span className="wnew__ts">{i.ts}</span>{i.text}
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {/* structural change arrives BATCHED — one proposal, one sitting.
            Hysteresis, not weather: proposed only after the preference held
            across cycles, priced in what it costs the READER, with the
            rejected alternative shown; "never" writes a rule. */}
        {w.rfc && rfcState === 'open' && (
          <section className="rfc">
            <div className="rfc__head">
              <span className="rfc__title">{w.rfc.title}</span>
              <span className="rfc__note">{w.rfc.note}</span>
            </div>
            {w.rfc.items.map((it, i) => (
              <div key={i} className="rfc__item">
                <div className="rfc__line1">
                  <span className={`rfc__verb`}>{it.verb}</span>
                  <span className="rfc__what">{it.what}</span>
                  <span className={`rfc__cls rfc__cls--${it.cls}`}
                        title={it.cls === 2
                          ? 'class 2 — local and reversible BY DEFINITION, so it may apply by default: the timer is visible, the veto is one click, and it pauses while you are away'
                          : 'class 3 — structural; never auto, never expires, waits indefinitely'}>
                    {it.cls === 2 ? (it.expires ?? 'applies by default · veto') : 'waits for you'}
                  </span>
                </div>
                <div className="rfc__row"><b>why</b><span>{it.why}</span></div>
                <div className="rfc__row"><b>costs you</b><span>{it.impact}</span></div>
                {it.alt && <div className="rfc__row rfc__row--alt"><b>also considered</b><span>{it.alt}</span></div>}
              </div>
            ))}
            <div className="rfc__actions">
              <button className="btn btn--primary" onClick={() => setRfcState('accepted')}>accept both</button>
              <button className="btn" onClick={() => setRfcState('partial')}>accept the fold only</button>
              <button className="btn" onClick={() => setRfcState('deferred')}
                      title="held — class-3 items wait indefinitely; the class-2 timer pauses">not yet</button>
              <button className="btn" onClick={() => setRfcState('never')}
                      title="writes a rule — this will not be proposed again unless the evidence moves">never</button>
            </div>
          </section>
        )}
        {w.rfc && rfcState !== 'open' && (
          <section className="rfc rfc--done">
            <span>
              {rfcState === 'accepted' && 'restructure applied — nothing to relearn: links are entity-addressed, the tray count is unchanged (it was always derived, never positional)'}
              {rfcState === 'partial' && 'the fold applied — no words moved, the fold selects the rendition you already ratified; the split stays on the table (class 3 waits, it will not nag)'}
              {rfcState === 'deferred' && 'held — the class-2 timer paused; both items wait for your next visit'}
              {rfcState === 'never' && 'noted as a rule — “don’t re-propose unless the evidence moves” written to the charter; ceremony for structural proposals here raised'}
            </span>
            <button className="btn" onClick={() => setRfcState('open')}>undo</button>
          </section>
        )}

        {/* ---------- spine: the project-grain face ---------- */}
        {w.spine && (
          <>
            <div className="stratum">
              <div className="stratum__rule">
                <span>the story so far — project grain</span>
                <em>rolling synthesis · ratified · supersedes, never rewrites</em>
              </div>
              <div className="spabs">
                {w.spine.abstract.map((p, i) => <p key={i}>{renderRefs(p.text, ctx)}</p>)}
                <div className="npara__sig">
                  {w.spine.synthesisNote}
                  {w.spine.superseded && (
                    <button className="spabs__prev" onClick={() => setPrevSyn(o => !o)}
                            title="consolidation is a ratification event — each synthesis supersedes the last; nothing is rewritten, everything archives beneath, still cited">
                      {prevSyn ? '▾' : '▸'} {w.spine.superseded.label}
                    </button>
                  )}
                </div>
                {prevSyn && w.spine.superseded && (
                  <div className="spabs__arch">{w.spine.superseded.note}</div>
                )}
              </div>
            </div>
            <div className="stratum">
              <div className="stratum__rule">
                <span>the arcs</span>
                <em>every question one line · the face follows the state · detail lives one level down</em>
              </div>
              {w.spine.arcs.map(a => (
                <SpineArcBlock key={a.id} arc={a} ctx={ctx} onAdvance={onAdvance}
                  badgeFor={deltaBadge} rollup={arcRollup(a)} />
              ))}
            </div>
          </>
        )}

        {/* ---------- stratum 1: narrative ---------- */}
        {!w.spine && w.sections.length > 0 && (
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
        {!w.spine && (w.trails.length > 0 || w.looseNotes.length > 0) && (
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
                      {n.draft && (accepted.has(n.id)
                        ? <span className="draftb draftb--done">✓ filed</span>
                        : <button className="draftb draftb--act" onClick={() => ctx.accept(n.id)}
                                  title="routine drafts file in place — one click, undoable from the tray">draft — file ✓</button>)}
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
                        <span className="sedsess__glyph"><SessGlyph live={s.state === 'open'} /></span>
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
            {w.sedimentTotal && w.sedimentTotal > w.sediment.length &&
              <> · showing the recent window — all {w.sedimentTotal.toLocaleString()} runs in the archive, searchable</>}
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

      {/* ---------- ⌘K omnibox: ask or find, from anywhere ---------- */}
      {omni.open && (
        <div className="omni" onClick={e => { if (e.target === e.currentTarget) setOmni(o => ({ ...o, open: false })) }}>
          <div className="omni__box">
            <input autoFocus value={omni.q} placeholder="Ask Guide, or find anything — “did the hold-out land?”…"
                   onChange={e => setOmni(o => ({ ...o, q: e.target.value, asked: false }))} />
            {omni.q.trim() && (
              <div className="omni__hits">
                <button className="omni__ask" onClick={() => setOmni(o => ({ ...o, asked: true }))}>
                  ✦ ask Guide: “{omni.q.trim()}”
                </button>
                {omni.asked && (
                  <div className="omni__answer">
                    Canned in the storyboard — in the real system this asks with the whole
                    Record in scope. Typing is the first move from anywhere, not just day 0.
                  </div>
                )}
                {searchRecord(w, omni.q, 'everything').map((h, i) => (
                  <button key={i} className="toc__hit" onClick={() => {
                    if (h.sess) ctx.openSession(h.sess.id, h.sess.turn)
                    else if (h.domId) { setSessPage(null); const id = h.domId; setTimeout(() => scrollTo(id), 80) }
                    setOmni(o => ({ ...o, open: false }))
                  }}>
                    <span className={`toc__hit-stratum toc__hit-stratum--${h.stratum}`}>{h.stratum}</span>
                    {h.label}
                  </button>
                ))}
              </div>
            )}
            <div className="omni__note">⌘K from anywhere · finds what was said, not just what was kept · Esc closes</div>
          </div>
        </div>
      )}
    </div>
  )
}
