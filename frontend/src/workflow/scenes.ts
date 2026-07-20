/**
 * The workflow storyboard — ten moments of a scientist working THROUGH the
 * Record (not beside it). Each scene is a full World: the document exactly
 * as it stands at that moment, plus what the scientist has open (desk,
 * working panel, archived transcripts).
 *
 * Part I — EARLY DAYS (day 0–3): the hard case, nothing to anchor on.
 *   The project is born as a composer; the document builds itself from the
 *   first exchange; noticing becomes notes; the first question is born
 *   mid-conversation; day 3 reads as a lab diary.
 *
 * Part II — MATURE (month 4): the coastal world as it stands.
 *   Re-entry and orientation; a session opened from a question with its
 *   scope already in hand; the churn loop with live accretion; session
 *   close as a distillation moment; the morning after — work findable
 *   from what it touched.
 */
import {
  provenance as coastalProv, benchFallback,
  type SedimentEntry, type Section, type Trail, type LooseNote, type Prov,
} from '../notebook/fixture'
import { coastalWorld, type World, type PanelState, type PanelMsg, type SessionRec } from '../notebook/world'

export interface Scene {
  id: string
  group: 'early' | 'mature'
  title: string        // step pill label
  narration: string    // caption under the storyboard bar
  /** interactive advance: which trigger moves the story forward, and the hint shown */
  advance?: { on: string; hint: string }
  world: World
}

const titlesOf = (sed: SedimentEntry[]) =>
  Object.fromEntries(sed.flatMap(s => s.shown.map(o => [o.id, o.title])))

// =======================================================================
// PART I — EARLY DAYS. Day 0: nothing exists. No datasets, no questions,
// no anchors. The composer is the whole interface.
// =======================================================================

const P0 = { title: 'Coastal sensor study', started: '2026-03-02', lastVisit: '2026-03-02' }

/** Early provenance: same assets, day-1 run identities. */
const eProv: Record<string, Prov> = {}
for (const id of ['fig_qc_flag1', 'fig_qc_flag2', 'fig_qc_flag3', 'fig_qc_ok3']) {
  eProv[id] = {
    ...coastalProv[id],
    runTitle: 'Intake & QC — batches 1–6',
    date: 'Mar 02 · 6 min', placement: 'ran locally · 4 GB',
    inputs: [{ id: 'ds_b16', title: 'Sensor readings — batches 1–6' }],
  }
}
for (const id of ['fig_seasonal', 'fig_calcurve']) {
  eProv[id] = { ...coastalProv[id], date: 'Mar 03 · 11 min' }
}

const emptyWorld: Omit<World, 'project'> = {
  whatsNew: null, pendingDrafts: 0, claims: {}, sections: [], trails: [],
  looseNotes: [], sediment: [], provenance: {}, figureTitles: {},
  bench: {}, benchFallback, onePager: null, work: true,
}

// ---- E1 · day 0: a new project is a composer, not a document
const e1: Scene = {
  id: 'e1', group: 'early', title: 'day 0 — begin',
  narration:
    'A new project is the hard case — there is nothing to anchor on, so the Record ' +
    'imposes nothing: no template, no empty sections to fill. The composer is the whole ' +
    'interface; the document will build itself from the work.',
  advance: { on: 'start', hint: 'type anything and press Enter — the story continues' },
  world: { project: P0, ...emptyWorld, bare: true },
}

// ---- E2 · work starts as talk; the document records it instantly
const e2Sediment: SedimentEntry[] = [
  {
    id: 'e_intake', date: 'Mar 02', title: 'Intake & QC — batches 1–6',
    state: 'running', verdict: 'running locally — completeness, response, gaps',
    nOutputs: 0, shown: [], retention: 'temporary', isNew: true,
  },
]
const e2Panel: PanelState = {
  scope: [{ kind: 'project', label: 'Coastal sensor study' }],
  scopeNote: 'nothing to anchor on yet — the project itself is the scope; that is fine, anchors are born from work, not declared',
  status: 'session open · 4 min',
  msgs: [
    { role: 'you', text: 'Six months of readings from 48 coastal sensors — batches 1–6, one file per batch, plus a station weather reference. Load them and take a first look.' },
    { role: 'guide', text: 'Registered two datasets: sensor readings (batches 1–6, 4.1 GB) and the station weather table. Starting an intake + QC sweep — completeness, response shape, gap structure per sensor. About 6 minutes locally.' },
    { run: { title: 'Intake & QC — batches 1–6', state: 'running', meta: 'local · started 09:12' }, role: 'system' },
    { note: 'recorded in the sediment the moment it launched ↓', role: 'system' },
  ],
}
const e2: Scene = {
  id: 'e2', group: 'early', title: 'first exchange',
  narration:
    'Work starts as talk — no forms, no setup. But look at the document beneath: the run ' +
    'became the sediment’s first line AT LAUNCH, not after curation. Tracking is automatic ' +
    'from minute one; the conversation is the instrument, the document is the record.',
  world: {
    project: P0, ...emptyWorld,
    sediment: e2Sediment, panel: e2Panel,
  },
}

// ---- E3 · results return to the conversation; noticing becomes notes
const e3Sediment: SedimentEntry[] = [
  {
    id: 'e_intake', date: 'Mar 02', title: 'Intake & QC — batches 1–6',
    state: 'ok', verdict: 'acceptable — 3 of 48 sensors flagged (14, 22, 31)',
    nOutputs: 104,
    shown: [
      { id: 'fig_qc_flag1', kind: 'figure', title: 'Sensor 14 — bimodal (flagged)', flagged: true },
      { id: 'fig_qc_flag2', kind: 'figure', title: 'Sensor 22 — heavy tail (flagged)', flagged: true },
      { id: 'fig_qc_flag3', kind: 'figure', title: 'Sensor 31 — dropout gaps (flagged)', flagged: true },
      { id: 'fig_qc_ok3', kind: 'figure', title: 'Batches 1–6 — completeness by day' },
    ],
    retention: 'temporary', isNew: true,
  },
]
const e3Notes: LooseNote[] = [
  {
    id: 'n1', ts: 'Mar 02', origin: 'guide', draft: true,
    text: 'Sensor 14 response is bimodal — the lower mode sits exactly at the factory calibration value. Worth watching across batches.',
    ref: 'fig_qc_flag1',
  },
  {
    id: 'n2', ts: 'Mar 02', origin: 'guide', draft: true,
    text: 'Sensor 31 drops out in 6.8% of intervals, always the same 40-minute window after midnight — looks like a logger duty cycle, not weather.',
    ref: 'fig_qc_flag3',
  },
]
const e3Panel: PanelState = {
  ...e2Panel,
  status: 'session open · 12 min',
  msgs: [
    ...e2Panel.msgs.slice(0, 3),
    { run: { title: 'Intake & QC — batches 1–6', state: 'ok', meta: '6 min · 104 outputs' }, role: 'system' },
    { role: 'guide', text: 'QC is back: acceptable overall — 3 of 48 sensors flagged. Sensor 14’s response is bimodal, 22 is heavy-tailed, 31 drops out nightly in the same 40-minute window. The other 101 outputs are unremarkable.' },
    { fig: { id: 'fig_qc_flag1', stat: 'sensor 14 — bimodal response (KS 0.41)' }, role: 'system' },
    { role: 'you', text: 'That lower mode on 14 is suspicious — keep an eye on it. And the 31 dropouts sound mechanical, logger-side.' },
    { role: 'guide', text: 'Noted both, each pointed at its panel — as notes, claiming nothing. They sit in the field-notes stratum until they earn more.' },
    { note: '2 field notes drafted → field notes ↓', role: 'system' },
  ],
}
const e3: Scene = {
  id: 'e3', group: 'early', title: 'noticing → notes',
  narration:
    'Results return INTO the conversation you are already having, and what you notice ' +
    'becomes a NOTE — dated, pointed at its panel, claiming nothing. The compression is ' +
    'structural: 104 outputs → one sediment line + two notes. The field-notes stratum was ' +
    'just born, from work, not from a template.',
  world: {
    project: P0, ...emptyWorld,
    sediment: e3Sediment, figureTitles: titlesOf(e3Sediment), provenance: eProv,
    looseNotes: e3Notes, panel: e3Panel, openSediment: ['e_intake'], pendingDrafts: 2,
  },
}

// ---- E4 · the first question is born mid-conversation
const e4Sections: Section[] = [
  {
    id: 'q1', question: 'Is the calibration stable across seasons?', phase: 'early',
    paragraphs: [], addenda: [],
    open: [
      'Does gain vary by season, or only with temperature?',
      'Are winter months comparable to summer at all?',
    ],
  },
]
const e4Sediment: SedimentEntry[] = [
  {
    id: 'e_seasonal', date: 'Mar 02', title: 'Seasonal decomposition — batches 1–6',
    state: 'running', verdict: 'running on hpc — STL, robust', nOutputs: 0, shown: [],
    retention: 'temporary', site: 'hpc', isNew: true,
  },
  { ...e3Sediment[0], isNew: false },
]
const e4Panel: PanelState = {
  ...e2Panel,
  status: 'session open · 19 min',
  msgs: [
    ...e3Panel.msgs.slice(0, 9),
    { role: 'you', text: 'What we’re actually after: is the calibration stable across seasons? Year-round comparability is what the grant hinges on.' },
    { role: 'guide', text: 'Opened it as the project’s first question — a stub for now; the story gets written from evidence, not ahead of it. A first useful cut: seasonal decomposition on batches 1–6, then gain variance by season. Queue it?' },
    { role: 'you', text: 'Queue it.' },
    { run: { title: 'Seasonal decomposition — batches 1–6', state: 'running', meta: 'hpc · started 09:31 · ~10 min' }, role: 'system' },
    { note: 'question opened → the story so far ↓ (a stub — nothing is known yet)', role: 'system' },
  ],
}
const e4: Scene = {
  id: 'e4', group: 'early', title: 'a question is born',
  narration:
    'Questions are born mid-conversation, from the work — not from a setup wizard. ' +
    'The story stratum appears with a STUB: the question and its open sub-questions, no ' +
    'prose. The Record never renders scaffolding for what it does not have; a nearly ' +
    'empty story is an honest face, not a broken one.',
  world: {
    project: P0, ...emptyWorld,
    sections: e4Sections,
    sediment: e4Sediment, figureTitles: titlesOf(e4Sediment), provenance: eProv,
    looseNotes: e3Notes, panel: e4Panel, pendingDrafts: 2,
  },
}

// ---- E5 · day 3: the lab-diary face
const e5Trails: Trail[] = [
  {
    id: 'T1', title: 'Something is off in the seasonal component', state: 'accumulating',
    fragments: [
      { ts: 'Mar 03', text: 'Winter panels of the decomposition are noisier than shot noise alone would predict.', ref: 'fig_seasonal' },
      { ts: 'Mar 04', text: 'Detrending kills the summer variance but winter residuals stay structured (LB p = 0.003).', ref: 'fig_calcurve', draft: true },
    ],
  },
]
const e5Sediment: SedimentEntry[] = [
  {
    id: 'e_pressure', date: 'Mar 04', title: 'Pressure spike check',
    state: 'ok', verdict: 'midnight spikes are quantized — logger artifact, parked',
    nOutputs: 4, shown: [], retention: 'temporary',
  },
  {
    id: 'e_gainvar', date: 'Mar 03', title: 'Gain variance by season',
    state: 'ok', verdict: 'summer < 2% between batches; winter 7–9%',
    nOutputs: 3, shown: [], retention: 'kept', site: 'hpc', sessionRef: 'seasonal first cut',
  },
  {
    id: 'e_seasonal', date: 'Mar 03', title: 'Seasonal decomposition — batches 1–6',
    state: 'ok', verdict: 'stable summer gain (1.9% var); winter panels noisy',
    nOutputs: 15,
    shown: [
      { id: 'fig_seasonal', kind: 'figure', title: 'Detrended series' },
      { id: 'fig_calcurve', kind: 'figure', title: 'Calibration curve by season' },
    ],
    retention: 'kept', site: 'hpc', trailRef: 'T1', sessionRef: 'seasonal first cut',
  },
  {
    id: 'e_join_failed', date: 'Mar 02', title: 'Weather join — first attempt',
    state: 'failed', verdict: '✗ station-id mismatch between feeds (fixed in the Mar 03 rerun)',
    nOutputs: 0, shown: [], retention: 'temporary',
  },
  { ...e3Sediment[0], isNew: false },
]
const seasonalCut: SessionRec = {
  id: 'seasonal first cut', label: 'seasonal first cut', when: 'Mar 03', state: 'filed',
  anchor: { kind: 'question', label: 'Q1 · calibration across seasons' },
  scope: [{ kind: 'question', label: 'Q1 · calibration across seasons' }],
  turns: 2,
  msgs: [
    { role: 'you', text: 'Decomposition’s done — what does gain variance look like by season?' },
    { role: 'guide', text: 'Summer gain varies under 2% between batches; winter sits at 7–9%. The winter panels are also noisier than shot noise would predict — I drafted that onto a new trail (T1) rather than the story: it’s noticed, not believed.' },
    { run: { title: 'Gain variance by season', state: 'ok', meta: '2 min · hpc' }, role: 'system' },
    { note: 'fragment drafted → T1 · session filed under Q1 at close', role: 'system' },
  ],
  distillate: [{ text: '“Winter panels noisier than shot noise alone would predict”', dest: 'trail T1' }],
  leftovers: [{ id: 'fig_qc_ok2', title: 'Gain variance — station-level spread (unexamined)' }],
}
const e5: Scene = {
  id: 'e5', group: 'early', title: 'day 3 — lab diary',
  narration:
    'Day 3, no session open. The Record reads as a LAB DIARY: sediment and notes dominate, ' +
    'one question stands as a stub, a first trail has opened (with one agent-drafted ' +
    'fragment awaiting your eye). The desk remembers where you left off; yesterday’s ' +
    'session is filed under Q1 — click ▷ to reread it. An almost-empty story is the ' +
    'correct early face.',
  world: {
    project: { ...P0, lastVisit: '2026-03-02' },
    ...emptyWorld,
    whatsNew: {
      since: 'Mar 02',
      items: [
        { ts: 'Mar 03', text: 'seasonal decomposition — stable summer gain; winter panels noisy' },
        { ts: 'Mar 03', text: 'trail started — “Something is off in the seasonal component”' },
        { ts: 'Mar 04', text: 'pressure spikes: quantized — logger artifact, parked' },
      ],
    },
    sections: [{ ...e4Sections[0], sessions: [{ label: 'seasonal first cut', when: 'Mar 03', meta: '2 runs · 1 fragment' }] }],
    trails: e5Trails,
    looseNotes: e3Notes.map(n => ({ ...n, draft: false })),
    sediment: e5Sediment, figureTitles: titlesOf(e5Sediment), provenance: eProv,
    pendingDrafts: 1,
    desk: {
      line: 'no open session',
      items: [{ label: 'yesterday: “seasonal first cut”', meta: 'under Q1 · 2 runs · 1 fragment', action: 'transcript ▷', sessionId: 'seasonal first cut' }],
    },
    sessions: [seasonalCut],
    openSediment: ['e_seasonal'],
  },
}

// =======================================================================
// PART II — MATURE (month 4). The coastal world as the notebook knows it.
// =======================================================================

const mBase: World = { ...coastalWorld, work: true }

/** The Jul 18 sitting under Q2 — filed, distilled, findable. */
const anomalyDig: SessionRec = {
  id: 'anomaly cluster dig', label: 'anomaly cluster dig', when: 'Jul 18', state: 'filed',
  anchor: { kind: 'question', label: 'Q2 · anomaly cluster' },
  scope: [{ kind: 'question', label: 'Q2 · anomaly cluster' }, { kind: 'trail', label: 'T2 · estuary/tides' }],
  turns: 2,
  msgs: [
    { role: 'you', text: 'Join the daily anomaly counts against the tidal coefficient.' },
    { role: 'guide', text: 'ρ = 0.61 (p = 0.002) — daily counts track the tidal coefficient. Drafted the third fragment onto T2; the trail now reads coherent across six weeks.' },
    { run: { title: 'Tidal coefficient join', state: 'ok', meta: '2 min · hpc' }, role: 'system' },
    { note: 'fragment drafted → T2 · session filed under Q2', role: 'system' },
  ],
  distillate: [{ text: '“Daily anomaly counts track the tidal coefficient (ρ = 0.61)”', dest: 'trail T2' }],
  leftovers: [{ id: 'fig_qc_ok1', title: 'Join residuals by station (unexamined)' }],
}

/** Mature sediment: the Jul 18 join is marked with its producing session. */
const matureSed = coastalWorld.sediment.map(e =>
  e.id === 'run_springtide' ? { ...e, sessionRef: 'anomaly cluster dig', turnRef: 2 } : e)

// ---- M1 · re-entry: orient, then start work from where you stand
const m1: Scene = {
  id: 'm1', group: 'mature', title: 'month 4 — re-entry',
  narration:
    'Month 4, back after a week away. Orientation is READING: the delta strip, then the ' +
    'pending addendum — the decision carries its evidence in the prose. The desk holds ' +
    'the resume point. To pick up the winter thread you start work from where you stand:',
  advance: { on: 'work:q1', hint: 'click  work ▸  on the first question to open a session' },
  world: {
    ...mBase,
    sediment: matureSed,
    desk: {
      line: 'no open sessions',
      items: [{ label: 'last session: “anomaly cluster dig”', meta: 'Jul 18 · under Q2 · 1 run', action: 'transcript ▷', sessionId: 'anomaly cluster dig' }],
    },
    sessions: [anomalyDig],
  },
}

// ---- M2 · a session opens knowing its question
const m2NewSed: SedimentEntry = {
  id: 'run_excl', date: 'Jul 20', title: 'Winter refit — serviced stations excluded',
  state: 'running', verdict: 'running on hpc — n 402 → 311', nOutputs: 0, shown: [],
  retention: 'temporary', site: 'hpc', isNew: true, sessionRef: 'winter dig', turnRef: 2,
}
const m2Panel: PanelState = {
  scope: [
    { kind: 'question', label: 'Q1 · calibration across seasons' },
    { kind: 'result', label: 'R9 · +0.45 ± 0.07' },
    { kind: 'result', label: 'R12 · −0.32 ± 0.11' },
    { kind: 'trail', label: 'T1 · seasonal component' },
  ],
  scopeNote: 'opened from Q1 — the question, both fits, and the trail are already in scope; nothing to re-explain',
  status: 'session open · 2 min',
  msgs: [
    { role: 'you', text: 'Check whether the January service visits explain the winter flip — refit the winter subset excluding stations 8–14.' },
    { role: 'guide', text: 'Queued: winter subset, stations 8–14 excluded (n = 402 → 311), otherwise R12’s spec. ~3 min on hpc. If the flip is a service artifact, this refit should flatten toward zero.' },
    { run: { title: 'Winter refit — excl. serviced', state: 'running', meta: 'hpc · started 09:41' }, role: 'system' },
    { note: 'in the sediment already ↓ — tracked from launch, kept or not', role: 'system' },
  ],
}
/** The winter dig at successive moments — an OPEN session is on the record
 *  too (the by-session work record shows it live). */
const winterDigAt = (msgs: PanelMsg[], turns: number, state: SessionRec['state']): SessionRec => ({
  id: 'winter dig', label: 'winter dig', when: 'Jul 20', state,
  anchor: { kind: 'question', label: 'Q1 · calibration across seasons' },
  scope: m2Panel.scope, msgs, turns,
  distillate: [], leftovers: [],
  continues: 'the winter subset re-fit (R12) · Jul 16',
})

const m2: Scene = {
  id: 'm2', group: 'mature', title: 'into a session',
  narration:
    'The session opened FROM the question, so it starts knowing Q1, both fits, and the ' +
    'trail — zero context-setting. And the run is in the record before it finishes: the ' +
    'sediment’s top line landed at launch, marked with the session that produced it.',
  world: {
    ...mBase,
    sediment: [m2NewSed, ...matureSed],
    panel: m2Panel,
    sessions: [winterDigAt(m2Panel.msgs, 2, 'open'), anomalyDig],
    desk: { line: '1 open session', items: [{ label: 'Q1 · winter dig', meta: 'started 09:40 · 1 run in flight', live: true, sessionId: 'winter dig' }] },
    openSediment: [],
  },
}

// ---- M3 · the churn loop; the document accretes DURING the session
const m3Sed: SedimentEntry[] = [
  {
    id: 'run_perstation', date: 'Jul 20', title: 'Per-station winter slopes',
    state: 'running', verdict: 'running on hpc — 48 stations', nOutputs: 0, shown: [],
    retention: 'temporary', site: 'hpc', isNew: true, sessionRef: 'winter dig', turnRef: 4,
  },
  {
    id: 'run_b11', date: 'Jul 20', title: 'Winter refit — B11 alone',
    state: 'ok', verdict: '−0.27 ± 0.19 — wide (one station), same sign',
    nOutputs: 1, shown: [], retention: 'temporary', isNew: true, sessionRef: 'winter dig', turnRef: 4,
  },
  { ...m2NewSed, state: 'ok', verdict: 'slope −0.29 ± 0.12 — flip persists (n = 311)', nOutputs: 2 },
]
const m3Panel: PanelState = {
  ...m2Panel,
  status: 'session open · 41 min',
  msgs: [
    ...m2Panel.msgs.slice(0, 2),
    { run: { title: 'Winter refit — excl. serviced', state: 'ok', meta: '3 min · hpc' }, role: 'system' },
    { fig: { id: 'fig_winter', stat: 'slope −0.29 ± 0.12 — the flip persists without the serviced stations' }, role: 'system' },
    { role: 'you', text: 'So not the service visits. Try B11 alone — never serviced, decent coverage.' },
    { role: 'guide', text: 'B11 alone: −0.27 ± 0.19 — wide, it’s one station, but the same sign. Queued the per-station winter slopes to close this properly.' },
    { run: { title: 'Per-station winter slopes', state: 'running', meta: 'hpc · started 10:05' }, role: 'system' },
    { note: 'fragment drafted → T1 ↓ — “flip persists excluding serviced stations”', role: 'system' },
  ],
}
const m3Trails: Trail[] = coastalWorld.trails.map(t => t.id !== 'T1' ? t : {
  ...t,
  fragments: [
    ...t.fragments,
    { ts: 'Jul 20', text: 'Winter flip persists with serviced stations excluded (−0.29 ± 0.12) — the service-artifact reading weakens further.', ref: 'fig_winter', draft: true, src: { sess: 'winter dig', turn: 4 } },
  ],
})
const m3: Scene = {
  id: 'm3', group: 'mature', title: 'the churn loop',
  narration:
    'Ask, run, look, ask again — three runs in forty minutes. The dead ends STAY in the ' +
    'sediment (one line each, marked ▷ with their session); the one fragment that matters ' +
    'is already drafted onto trail T1, awaiting your eye. The document accretes DURING ' +
    'the session, not from an end-of-day writeup.',
  world: {
    ...mBase,
    sediment: [...m3Sed, ...matureSed],
    trails: m3Trails,
    panel: m3Panel,
    sessions: [winterDigAt(m3Panel.msgs, 4, 'open'), anomalyDig],
    desk: { line: '1 open session', items: [{ label: 'Q1 · winter dig', meta: 'started 09:40 · 3 runs · 1 fragment drafted', live: true, sessionId: 'winter dig' }] },
    openSediment: [],
  },
}

// ---- M4 · session close: the distillation moment
const m4Sed = [
  { ...m3Sed[0], state: 'ok' as const, verdict: 'winter sign flips at 31 of 48 stations — not a single-station artifact', nOutputs: 2, retention: 'kept' as const },
  m3Sed[1],
  { ...m3Sed[2], retention: 'kept' as const },
]
const m4Panel: PanelState = {
  ...m3Panel,
  status: 'closing · 48 min',
  msgs: [
    ...m3Panel.msgs.slice(0, 7),
    { run: { title: 'Per-station winter slopes', state: 'ok', meta: '9 min · hpc' }, role: 'system' },
    { role: 'guide', text: 'Per-station: the winter sign flips at 31 of 48 stations, serviced or not. This is real structure, not an artifact of the January visits. The 2025 hold-out (still running) stays the arbiter of mechanism.' },
  ],
  closing: {
    summary: '48 min · 5 runs (2 kept, 3 exploratory) · 1 fragment · 1 addendum draft · 1 keep proposal',
    distillates: [
      { text: '“Flip persists excluding serviced stations (−0.29 ± 0.12); 31/48 stations flip individually”', dest: 'trail T1', state: 'accepted' },
      { text: 'Addendum draft for Q1 — service-visit explanation ruled out; mechanism still open, hold-out pending', dest: 'Q1 · inbox', state: 'to inbox' },
      { text: 'Keep the two final refits durably; three exploratory outputs lapse in 30 d', dest: 'retention · inbox', state: 'to inbox' },
    ],
  },
}
const m4: Scene = {
  id: 'm4', group: 'mature', title: 'walking away',
  narration:
    'Closing a session is a DISTILLATION moment, not an exit. The panel proposes what ' +
    'enters the record — a trail fragment, an addendum draft, a retention decision — and ' +
    'nothing enters without your ratification. The transcript files under Q1, out of the ' +
    'way but never lost.',
  advance: { on: 'file-close', hint: 'click  file & close  in the panel to finish the session' },
  world: {
    ...mBase,
    sediment: [...m4Sed, ...matureSed],
    trails: m3Trails,
    panel: m4Panel,
    sessions: [winterDigAt(m4Panel.msgs, 5, 'open'), anomalyDig],
    desk: { line: '1 open session', items: [{ label: 'Q1 · winter dig', meta: 'closing — distillate proposed', live: true, sessionId: 'winter dig' }] },
    openSediment: [],
  },
}

// ---- M5 · the morning after: findable from what it touched
const m5Sed = m4Sed.map(e => ({ ...e, isNew: false }))

/** The winter dig, filed: distillate recorded, leftovers counted, chain kept. */
const winterDigFiled: SessionRec = {
  ...winterDigAt(m4Panel.msgs, 5, 'filed'),
  distillate: [
    { text: '“Flip persists excluding serviced stations (−0.29 ± 0.12); 31/48 stations flip individually”', dest: 'trail T1' },
    { text: 'Addendum for Q1 — service-visit explanation ruled out; hold-out pending', dest: 'Q1 · awaiting ratification' },
    { text: 'Keep the two final refits durably; three exploratory outputs lapse in 30 d', dest: 'retention · awaiting ratification' },
  ],
  leftovers: [
    { id: 'fig_qc_ok1', title: 'Per-station slopes vs distance to coast (unexamined)', note: 'gradient visible — may bear on Q2, the estuary cluster' },
    { id: 'fig_qc_ok2', title: 'Excluded-refit residuals — QQ (unexamined)' },
  ],
}

const m5: Scene = {
  id: 'm5', group: 'mature', title: 'next morning',
  narration:
    'Next morning the DOCUMENT is the resume point — and the work record now reads BY ' +
    'SESSION: each sitting one super-row (turns · runs · distilled · UNEXAMINED count), ' +
    'its runs nested, solo runs standing apart. Yesterday’s session is one click away ' +
    'from Q1 (▷), the desk, every sediment line it produced — and the T1 fragment now ' +
    'carries “▷ turn 4”: provenance for prose, at turn grain. Try searching “never ' +
    'serviced” — what was SAID is findable, not just what was kept.',
  world: {
    ...mBase,
    project: { ...coastalWorld.project, lastVisit: '2026-07-19' },
    whatsNew: {
      since: 'Jul 19',
      items: [
        { ts: 'Jul 20', text: 'winter flip is NOT a service artifact — refit excluding serviced stations, 31/48 stations flip (session: winter dig)' },
        { ts: 'Jul 20', text: 'addendum drafted for Q1 — awaiting ratification' },
        { ts: 'today', text: 'hold-out check on 2025 data — still running on hpc', live: true },
      ],
    },
    pendingDrafts: 2,
    sections: coastalWorld.sections.map(s => s.id !== 'q1' ? s : {
      ...s,
      sessions: [{ label: 'winter dig', when: 'Jul 20', meta: '5 runs · 1 fragment · 1 draft' }],
      addenda: [
        ...s.addenda,
        {
          id: 'q1a2', on: 'Jul 20', status: 'pending' as const,
          text: 'The winter flip is not a service artifact: excluding the serviced stations preserves it (−0.29 ± 0.12, [[fig:fig_winter|refit]]), and the sign flips at 31 of 48 stations individually. The mechanism question stays open; the 2025 hold-out ([[run:run_holdout|running]]) remains the arbiter.',
        },
      ],
    }),
    trails: m3Trails.map(t => t.id !== 'T1' ? t : {
      ...t, fragments: t.fragments.map(f => ({ ...f, draft: false })),
    }),
    sediment: [...m5Sed, ...matureSed],
    desk: {
      line: 'no open sessions',
      items: [{ label: 'yesterday: “winter dig”', meta: 'under Q1 · 5 runs · 1 fragment', action: 'transcript ▷', sessionId: 'winter dig' }],
    },
    sessions: [winterDigFiled, anomalyDig],
    sedimentGrain: 'session',
    openSediment: [],
  },
}

// ---- M6 · the session page: the territory behind the map
const m6: Scene = {
  id: 'm6', group: 'mature', title: 'the session page',
  narration:
    'A session on its own terms — full page for sifting, docked panel for working ' +
    '(⇥ / ⤢ convert between them). Distillate up top; the LEFTOVERS shelf: artifacts ' +
    'produced but never pinned, noted, or discussed — including one the agent flags as ' +
    'possibly bearing on Q2. Transcript turns are addressable (▷ links land here, ' +
    'highlighted), the chain edge records what this sitting continued, and the composer ' +
    'at the foot means filed ≠ dead.',
  world: {
    ...m5.world,
    openSession: { id: 'winter dig' },
  },
}

// ---- M7 · live anchoring: the document as a working surface
// The rules, all on one screen: the anchor wears a standing state; in-view
// changes land in place; out-of-view changes pulse the TOC and tick the
// delta rail (3 tiers); deixis is mutual (click the page → "looking at";
// message refs locate their element); cross-boundary relevance stays a
// proposal; hold ⌖ parks an excerpt on the desk. The page never scrolls
// itself — not even for the hold-out landing mid-session.
const m7Panel: PanelState = {
  ...m3Panel,
  status: 'session open · 52 min',
  touched: ['Q1', 'T1', 'sediment ×3'],
  lookingAt: 'Q1 · addendum (Jul 16)',
  crossFlag: {
    text: 'the per-station slopes also track distance to coast — may bear on Q2 (the estuary cluster)',
    accept: 'file a note → Q2',
  },
  msgs: [
    ...m3Panel.msgs,
    { run: { title: 'Per-station winter slopes', state: 'ok', meta: '9 min · hpc' }, role: 'system' },
    {
      role: 'guide',
      text: 'Per-station is in: the winter sign flips at 31 of 48 stations, serviced or not. The residual structure echoes what T1 has been collecting since June.',
      ref: { el: 'el-T1', label: 'show T1 on the page →' },
    },
  ],
}
const m7: Scene = {
  id: 'm7', group: 'mature', title: 'live anchoring',
  narration:
    'The document as a working surface. Q1 wears the standing “working here” state; ' +
    'changes OUT of view pulse the TOC and tick the delta rail (teal accretion · amber ' +
    'awaiting-you · red condition — the hold-out just landed, see what’s new; the page ' +
    'never scrolls itself). Deixis is mutual: click any figure or trail on the page and ' +
    'the panel’s “looking at:” follows; the agent’s message points back (“show T1 →”). ' +
    'The Q2 relevance stays a PROPOSAL; hold ⌖ parks the addendum on the desk.',
  world: {
    ...mBase,
    whatsNew: {
      since: 'Jul 12',
      items: [
        { ts: 'now', text: 'hold-out finished — winter flip CONFIRMED on 2025 data (−0.31 ± 0.09) → the contradiction resolves', loud: true },
        ...(coastalWorld.whatsNew?.items.filter(i => !i.live) ?? []),
      ],
    },
    sediment: [...m3Sed, ...matureSed],
    trails: m3Trails,
    panel: m7Panel,
    sessions: [winterDigAt(m7Panel.msgs, 5, 'open'), anomalyDig],
    desk: { line: '1 open session', items: [{ label: 'Q1 · winter dig', meta: 'started 09:40 · 3 runs · 1 fragment drafted', live: true, sessionId: 'winter dig' }] },
    anchorAt: { session: 'winter dig', elId: 'q1' },
    deltas: [
      { elId: 'el-q1', kind: 'condition', label: 'hold-out landed — resolves the winter contradiction (awaiting your read)' },
      { elId: 'el-T1', kind: 'draft', count: 1, label: 'T1: 1 fragment drafted this session — awaiting your eye' },
      { elId: 'el-q2', kind: 'draft', count: 1, label: 'Q2: proposed note from the winter dig (cross-boundary — not written yet)' },
      { elId: 'el-sediment', kind: 'accretion', count: 3, label: '3 runs landed this session' },
    ],
    openSediment: [],
  },
}

export const SCENES: Scene[] = [e1, e2, e3, e4, e5, m1, m2, m3, m4, m5, m6, m7]

export const GROUPS: { id: 'early' | 'mature'; label: string }[] = [
  { id: 'early', label: 'I · early days (day 0–3)' },
  { id: 'mature', label: 'II · mature project (month 4)' },
]
