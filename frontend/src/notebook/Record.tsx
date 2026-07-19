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
 */
import { Fragment as F, useMemo, useState, type ReactNode } from 'react'
import {
  project, whatsNew, pendingDrafts, claims, sections, trails, looseNotes,
  sediment, provenance, figureTitles, bench, benchFallback, onePager,
  type BenchMsg, type Section, type Trail,
} from './fixture'

const ART = (id: string) => `/artifacts/coastal/${id}.svg`

const MATURITY_GLYPH: Record<string, string> = {
  conjecture: '○', supported: '◐', 'cross-checked': '◕', robust: '●', contested: '◮',
}

// ---------------------------------------------------------------- ref parsing

interface RefCtx {
  openBench: (id: string, label: string) => void
  toggleDisclose: (id: string) => void
  disclosed: Set<string>
  scrollTo: (domId: string) => void
}

/** Render prose with [[kind:id|label]] live references. Block-level
 *  [[figure:id]] tokens are handled by splitBlocks() before this runs. */
function renderRefs(text: string, ctx: RefCtx): ReactNode[] {
  const out: ReactNode[] = []
  const re = /\[\[(fig|claim|run|trail):([^\]|]+)(?:\|([^\]]+))?\]\]/g
  let last = 0, m: RegExpExecArray | null, k = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(<F key={k++}>{text.slice(last, m.index)}</F>)
    const [, kind, id, label] = m
    if (kind === 'fig') {
      out.push(
        <button key={k++} className="ref ref--fig" title={`${figureTitles[id] ?? id} — click to open the figure and its provenance`}
                onClick={() => ctx.toggleDisclose(id)}>
          {label ?? figureTitles[id] ?? id}
        </button>)
    } else if (kind === 'claim') {
      const c = claims[id]
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
      const t = trails.find(x => x.id === id)
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

function ProvDrawer({ figId }: { figId: string }) {
  const p = provenance[figId]
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
  return (
    <figure className="fig" id={`el-${figId}`}>
      <img src={ART(figId)} alt={figureTitles[figId] ?? figId} />
      <figcaption>
        <span>{caption ?? figureTitles[figId] ?? figId}</span>
        <span className="fig__actions">
          <button onClick={() => ctx.toggleDisclose(figId)} title="the technical record: producing run, code, params, environment, log">
            {open ? 'close ▴' : 'how was this made? ▾'}
          </button>
          <button onClick={() => ctx.openBench(figId, figureTitles[figId] ?? figId)} title="open the margin bench on this element">
            ask ✦
          </button>
        </span>
      </figcaption>
      {open && <ProvDrawer figId={figId} />}
    </figure>
  )
}

// ------------------------------------------------------------------ sections

function NarrativeSection({ s, ctx, methods, onMethods, onRatify, ratified }: {
  s: Section; ctx: RefCtx
  methods: boolean; onMethods: () => void
  onRatify: (id: string) => void
  ratified: Set<string>
}) {
  const phaseNote = { early: 'early — mostly noticing', mid: 'mid — condensing', late: 'late — writing up' }[s.phase]
  return (
    <section className="nsec" id={`el-${s.id}`}>
      <div className="nsec__head">
        <h3>{s.question}</h3>
        <span className="nsec__phase" title="phase is per-question, derived from content — a young question in an old project is simply early">{phaseNote}</span>
        <button className={`nsec__methods ${methods ? 'is-on' : ''}`} onClick={onMethods}
                title="expand every referenced result into its methods detail — generated from provenance, never hand-maintained">
          methods mode
        </button>
      </div>
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
                  <span className="npara__method-fig">{figureTitles[fid] ?? fid}:</span>{' '}
                  {provenance[fid]
                    ? `${provenance[fid].runTitle} — ${Object.entries(provenance[fid].params).map(([k, v]) => `${k}=${v}`).join(', ')} · ${provenance[fid].env.packages.join(', ')} · ${provenance[fid].env.fingerprint}`
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
                <button className="btn" onClick={() => ctx.openBench(a.id, 'addendum — winter contradiction')}>discuss ✦</button>
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
        <h4>{t.title}</h4>
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
                  {figureTitles[f.ref] ?? f.ref}
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
  e: (typeof sediment)[number]; ctx: RefCtx; open: boolean; onToggle: () => void
}) {
  const ret = {
    kept: { label: `kept ✓${e.site ? ` on ${e.site}` : ''}`, cls: 'ok' },
    temporary: { label: 'temporary', cls: 'mut' },
    'at-risk': { label: 'at risk', cls: 'risk' },
  }[e.retention]
  return (
    <div className={`sed ${e.state === 'failed' ? 'sed--failed' : ''}`} id={`el-${e.id}`}>
      <button className="sed__line" onClick={onToggle} title={open ? 'collapse' : `expand ${e.nOutputs} outputs`}>
        <span className="sed__date">{e.date}</span>
        <span className={`sed__state sed__state--${e.state}`}>
          {e.state === 'running' ? '▶' : e.state === 'failed' ? '✗' : '·'}
        </span>
        <span className="sed__title">{e.title}</span>
        <span className="sed__verdict">{e.verdict}</span>
        <span className="sed__n">{e.nOutputs > 0 ? `${e.nOutputs} outputs` : ''}</span>
        {e.trailRef && <span className="sed__trail" title={`feeds trail ${e.trailRef}`}>⋱ {e.trailRef}</span>}
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

function MarginBench({ target, onClose }: {
  target: { id: string; label: string }; onClose: () => void
}) {
  const canned = bench[target.id] ?? benchFallback
  const [extra, setExtra] = useState<BenchMsg[]>([])
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

interface Hit { domId: string; label: string; stratum: string }
function searchRecord(q: string, scope: 'story' | 'noticed' | 'everything'): Hit[] {
  const needle = q.toLowerCase()
  const hits: Hit[] = []
  const has = (s: string) => s.toLowerCase().includes(needle)
  if (scope === 'story' || scope === 'everything') {
    for (const s of sections) {
      for (const p of s.paragraphs) if (has(p.text) || has(s.question)) {
        hits.push({ domId: `el-${p.id}`, label: `${s.question} — §`, stratum: 'story' }); break
      }
      for (const a of s.addenda) if (has(a.text)) hits.push({ domId: `el-${a.id}`, label: 'addendum (winter contradiction)', stratum: 'story' })
    }
  }
  if (scope === 'noticed' || scope === 'everything') {
    for (const t of trails) if (has(t.title) || t.fragments.some(f => has(f.text)))
      hits.push({ domId: `el-${t.id}`, label: `${t.id} · ${t.title}`, stratum: 'noticed' })
    for (const n of looseNotes) if (has(n.text))
      hits.push({ domId: `el-note-${n.id}`, label: n.text.slice(0, 48) + '…', stratum: 'noticed' })
  }
  if (scope === 'everything') {
    for (const e of sediment) if (has(e.title) || has(e.verdict))
      hits.push({ domId: `el-${e.id}`, label: `${e.date} · ${e.title}`, stratum: 'sediment' })
  }
  return hits.slice(0, 8)
}

// ---------------------------------------------------------------------- root

export default function Record() {
  const [benchFor, setBenchFor] = useState<{ id: string; label: string } | null>(null)
  const [disclosed, setDisclosed] = useState<Set<string>>(new Set())
  const [methodsOn, setMethodsOn] = useState<Set<string>>(new Set())
  const [openSed, setOpenSed] = useState<Set<string>>(new Set(['run_qc']))
  const [ratified, setRatified] = useState<Set<string>>(new Set())
  const [drafted, setDrafted] = useState<Set<string>>(new Set())
  const [view, setView] = useState<'record' | 'onepager'>(
    () => new URLSearchParams(window.location.search).get('view') === 'onepager' ? 'onepager' : 'record')
  const [newOpen, setNewOpen] = useState(false)
  const [q, setQ] = useState('')
  const [scope, setScope] = useState<'story' | 'noticed' | 'everything'>('everything')
  const [activeAnchor, setActiveAnchor] = useState('')

  // TOC tracks the reader's position: the anchor nearest above the upper
  // third of the document column is "where you are".
  const ANCHORS = [
    ...sections.map(s => `el-${s.id}`),
    ...trails.map(t => `el-${t.id}`),
    'el-loose', 'el-sediment',
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
  const ctx: RefCtx = {
    openBench: (id, label) => setBenchFor({ id, label }),
    toggleDisclose: id => setDisclosed(s => toggle(s, id)),
    disclosed,
    scrollTo,
  }
  const hits = useMemo(() => (q.trim() ? searchRecord(q, scope) : []), [q, scope])
  const behindLine = pendingDrafts > 0
    ? `${pendingDrafts} drafts waiting — the record is ~${Math.max(1, Math.round(pendingDrafts * 1.2))} days behind the work`
    : 'the record is current'

  // ------------------------------------------------ one-pager (§2.4)
  if (view === 'onepager') {
    return (
      <div className="rec rec--onepager">
        <main className="doc doc--onepager">
          <div className="doc__viewbar">
            <button className="btn" onClick={() => setView('record')}>← full record</button>
            <span className="doc__viewnote">the p-value visitor's render — same machinery, thin project; nothing was imposed to get here</span>
          </div>
          <h1>{project.title}</h1>
          <p className="op__data"><b>Data.</b> {onePager.dataLine}</p>
          <p className="op__method"><b>Method.</b> {onePager.methodLine}</p>
          <div className="op__number">{onePager.number}</div>
          <p className="op__caveat"><b>Caveat.</b> {onePager.caveat}</p>
          <div className="op__sig">assembled from the record · {sediment.length} runs in the sediment appendix · print and take it to the meeting</div>
          <section className="op__appendix">
            <h2>Sediment appendix</h2>
            {sediment.map(e => (
              <div key={e.id} className="op__sedline">
                <span className="sed__date">{e.date}</span> {e.title} — {e.verdict}
              </div>
            ))}
          </section>
        </main>
      </div>
    )
  }

  return (
    <div className={`rec ${benchFor ? 'rec--bench' : ''}`}>
      {/* ---------- contents rail ---------- */}
      <nav className="toc">
        <div className="toc__title">The Record</div>
        <div className="toc__project">{project.title}</div>
        <div className="toc__since">since {project.started.slice(0, 7)}</div>

        <div className="toc__group">story so far</div>
        {sections.map(s => (
          <button key={s.id} className={`toc__item ${activeAnchor === `el-${s.id}` ? 'is-active' : ''}`} onClick={() => scrollTo(`el-${s.id}`)}>
            <span className={`toc__phase toc__phase--${s.phase}`} />
            {s.question}
          </button>
        ))}
        <div className="toc__group">field notes</div>
        {trails.map(t => (
          <button key={t.id} className={`toc__item ${activeAnchor === `el-${t.id}` ? 'is-active' : ''}`} onClick={() => scrollTo(`el-${t.id}`)}>
            <span className="toc__trail">⋱</span> {t.id} · {t.title}
          </button>
        ))}
        <button className={`toc__item ${activeAnchor === 'el-loose' ? 'is-active' : ''}`} onClick={() => scrollTo('el-loose')}>
          <span className="toc__trail">·</span> loose notes
        </button>
        <div className="toc__group">sediment</div>
        <button className={`toc__item ${activeAnchor === 'el-sediment' ? 'is-active' : ''}`} onClick={() => scrollTo('el-sediment')}>
          {sediment.length} runs · complete · automatic
        </button>

        <div className="toc__spacer" />
        <button className="toc__onepager" onClick={() => setView('onepager')}
                title="the same record rendered for the one-number visitor (§ focus spectrum)">
          view as one-pager
        </button>
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
                <button key={i} className="toc__hit" onClick={() => { scrollTo(h.domId); setQ('') }}>
                  <span className={`toc__hit-stratum toc__hit-stratum--${h.stratum}`}>{h.stratum}</span>
                  {h.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </nav>

      {/* ---------- the document ---------- */}
      <main className="doc" onScroll={onDocScroll}>
        <header className="doc__head">
          <h1>{project.title}</h1>
          <div className="doc__sub">
            a record co-written by you and Guide · narrative is yours to ratify · sediment keeps itself
          </div>
        </header>

        {/* what's new */}
        <section className={`wnew ${newOpen ? 'is-open' : ''}`}>
          <button className="wnew__head" onClick={() => setNewOpen(o => !o)}>
            <span className="wnew__count">what's new since {whatsNew.since} · {whatsNew.items.length}</span>
            <span className="wnew__peek">
              {whatsNew.items.filter(i => i.loud || i.live).map((i, k) => (
                <span key={k} className={`wnew__chip ${i.loud ? 'is-loud' : ''} ${i.live ? 'is-live' : ''}`}>
                  {i.live ? '▶ ' : i.loud ? '⚡ ' : ''}
                  {i.loud ? 'contradiction — R12 opposes R9' : i.text.split(' — ')[0]}
                </span>
              ))}
            </span>
            <span className="wnew__behind" title="degradation is visible and recoverable, never silent rot">{behindLine}</span>
            <span>{newOpen ? '▾' : '▸'}</span>
          </button>
          {newOpen && (
            <div className="wnew__body">
              {whatsNew.items.map((i, k) => (
                <div key={k} className={`wnew__item ${i.loud ? 'is-loud' : ''}`}>
                  <span className="wnew__ts">{i.ts}</span>{i.text}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ---------- stratum 1: narrative ---------- */}
        <div className="stratum">
          <div className="stratum__rule"><span>the story so far</span><em>ratified · sparse · load-bearing</em></div>
          {sections.map(s => (
            <NarrativeSection key={s.id} s={s} ctx={ctx}
              methods={methodsOn.has(s.id)}
              onMethods={() => setMethodsOn(x => toggle(x, s.id))}
              onRatify={id => setRatified(x => new Set(x).add(id))}
              ratified={ratified} />
          ))}
        </div>

        {/* ---------- stratum 2: field notes & trails ---------- */}
        <div className="stratum">
          <div className="stratum__rule"><span>field notes & trails</span><em>noticed ≠ believed · cheap · revisable</em></div>
          {trails.map(t => (
            <TrailCard key={t.id} t={t} ctx={ctx}
              drafted={drafted.has(t.id)}
              onDraft={() => setDrafted(x => new Set(x).add(t.id))} />
          ))}
          <div className="loose" id="el-loose">
            {looseNotes.map(n => (
              <div key={n.id} className="lnote" id={`el-note-${n.id}`}>
                <span className="lnote__ts">{n.ts}</span>
                <span className={`lnote__who lnote__who--${n.origin}`}>{n.origin === 'guide' ? '✦ Guide' : 'you'}</span>
                <span className="lnote__text">
                  {n.text}
                  {n.ref && (
                    <button className="ref ref--fig frag__ref" onClick={() => ctx.toggleDisclose(n.ref!)}>
                      {figureTitles[n.ref] ?? n.ref}
                    </button>
                  )}
                </span>
              </div>
            ))}
            {looseNotes.map(n => n.ref && disclosed.has(n.ref) && (
              <FigureEmbed key={n.ref} figId={n.ref} ctx={ctx} />
            ))}
            <div className="loose__sweep">free-floating notes get a weekly file-or-fade sweep — attach to a trail, a question, or let them fade</div>
          </div>
        </div>

        {/* ---------- stratum 3: sediment ---------- */}
        <div className="stratum" id="el-sediment">
          <div className="stratum__rule"><span>sediment</span><em>every run · one line each · nothing lost, nothing demands reading</em></div>
          <div className="sed-list">
            {sediment.map(e => (
              <SedimentRow key={e.id} e={e} ctx={ctx}
                open={openSed.has(e.id)}
                onToggle={() => setOpenSed(x => toggle(x, e.id))} />
            ))}
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

      {/* ---------- margin bench ---------- */}
      {/* keyed by anchor: each element's margin conversation is its own —
          switching targets must never carry the previous exchange along */}
      {benchFor && <MarginBench key={benchFor.id} target={benchFor} onClose={() => setBenchFor(null)} />}
    </div>
  )
}
