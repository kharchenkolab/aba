/**
 * The workflow storyboard — sixteen moments of a scientist working THROUGH
 * the Record (not beside it). Each scene is a full World: the document
 * exactly as it stands at that moment, plus what the scientist has open
 * (desk, working panel, archived transcripts).
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
import { coastalWorld, type World, type PanelState, type PanelMsg, type SessionRec, type Spine } from '../notebook/world'

export interface Scene {
  id: string
  group: 'early' | 'mature' | 'late'
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
        { ts: 'Mar 03', text: 'seasonal decomposition — stable summer gain; winter panels noisy', elId: 'el-e_seasonal' },
        { ts: 'Mar 03', text: 'trail started — “Something is off in the seasonal component”', elId: 'el-T1' },
        { ts: 'Mar 04', text: 'pressure spikes: quantized — logger artifact, parked', elId: 'el-e_pressure' },
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

// ---- E6 · the future is marked: intent precedes evidence
// The scientist knows where they intend to dig BEFORE the evidence is in.
// Declaring it CONVERTS, never refuses: a committed-direction stub whose
// content at this stage IS the plan — prose tracks evidence, structure
// tracks intent, and the sketch wears its future tense openly.
const e6Q2: Section = {
  id: 'q2', question: 'What drives the anomaly cluster?', phase: 'early',
  paragraphs: [], addenda: [],
  intent: { on: 'Mar 04' },
  planDraft: true,
  charge: 'Committed direction: the anomaly cluster is to become a main line. Prose follows the evidence; this shape is the plan.',
  authored: 'shape drafted by Guide · awaiting your ratification',
  open: [
    'Are the anomaly events spatially clustered?',
    'Do they track an external forcing — tides, weather, servicing?',
  ],
  plan: [
    { text: 'Spatial distribution of anomaly events against the coastline', state: 'planned' },
    { text: 'Tide / weather covariate join on daily counts', state: 'planned' },
    { text: 'Estuary-distance gradient — does amplitude decay with distance?', state: 'planned' },
    { text: 'Detector-bias check on storm days', state: 'planned' },
  ],
}
const e6Panel: PanelState = {
  scope: [{ kind: 'project', label: 'Coastal sensor study' }],
  status: 'session open · 3 min',
  msgs: [
    { role: 'you', text: 'Before it slips: the anomaly clustering I keep seeing in the QC panels — that will be an important piece of this study. I want it as a main line alongside calibration.' },
    { role: 'guide', text: 'Marked as a committed direction. There isn’t enough evidence to write anything yet — one flagged panel and a hunch — so the section opens as a stub and I’ve sketched the shape as a plan: four analyses that would establish it. The prose will follow the evidence; the shape is yours to ratify.' },
    { note: 'stub opened · draft plan proposed → the story so far ↓ (ratify the shape, not prose)', role: 'system' },
    { role: 'you', text: 'The sketch is right. Start with the spatial distribution once the decomposition’s done.' },
    { role: 'guide', text: 'Queued behind it. The other three stay planned — each carries its own ▷ work when you want it, and I can draft a fuller technical plan for any of them before a run ever starts.' },
  ],
}
const e6: Scene = {
  id: 'e6', group: 'early', title: 'the future is marked',
  narration:
    'The scientist knows where they intend to dig BEFORE the evidence exists — and saying so ' +
    'CONVERTS, never refuses: a committed-direction stub opens whose content, at this stage, IS ' +
    'the plan. Prose tracks evidence; structure tracks intent — the skeleton wears its future ' +
    'tense openly (a draft shape, ratified once), each planned analysis is a door and a ' +
    'launcher, and growth along this line is now pre-consented. The list itself is YOURS to ' +
    'work — type to add an item, click one to reword it, ✕ to park it: no ceremony for your ' +
    'own plan.',
  world: {
    project: { ...P0, lastVisit: '2026-03-02' },
    ...emptyWorld,
    whatsNew: {
      since: 'Mar 02',
      items: [
        { ts: 'Mar 04', text: 'committed direction marked — “anomaly cluster” · stub + draft plan (4 analyses)', elId: 'el-q2' },
        { ts: 'Mar 03', text: 'seasonal decomposition — stable summer gain; winter panels noisy', elId: 'el-e_seasonal' },
        { ts: 'Mar 03', text: 'trail started — “Something is off in the seasonal component”', elId: 'el-T1' },
      ],
    },
    sections: [{ ...e4Sections[0], sessions: [{ label: 'seasonal first cut', when: 'Mar 03', meta: '2 runs · 1 fragment' }] }, e6Q2],
    trails: e5Trails,
    looseNotes: e3Notes.map(n => ({ ...n, draft: false })),
    sediment: e5Sediment, figureTitles: titlesOf(e5Sediment), provenance: eProv,
    pendingDrafts: 2,
    panel: e6Panel,
    desk: {
      line: '1 open session',
      items: [{ label: 'project · marking directions', meta: 'started 08:55', live: true }],
    },
    sessions: [seasonalCut],
  },
}

// =======================================================================
// PART II — MATURE (month 4). The coastal world as the notebook knows it.
// =======================================================================

/** Mature what's-new with every item a DOOR (elId targets). */
const mWhatsNew = {
  since: 'Jul 12',
  items: [
    { ts: 'Jul 15', text: 'claim advanced — “Sensor drift is temperature-driven” → supported', elId: 'el-q1p2' },
    { ts: 'Jul 16', text: 'contradiction — R12 opposes R9 (opposite sign, winter subset)', loud: true, elId: 'el-q1a1' },
    { ts: 'Jul 17', text: 'QC sweep on batch 7 — 104 outputs, verdict acceptable, 3 flagged', elId: 'el-run_qc' },
    { ts: 'Jul 17', text: 'batch 7 upstream source changed (drift flag raised)', elId: 'el-run_qc' },
    { ts: 'Jul 18', text: 'tidal coefficient join — anomaly rate tracks tides (ρ = 0.61)', elId: 'el-run_springtide' },
    { ts: 'today', text: 'hold-out check on 2025 data started on hpc — running now', live: true, elId: 'el-run_holdout' },
  ],
}

const mBase: World = { ...coastalWorld, work: true, whatsNew: mWhatsNew, digestFig: 'fig_winter' }

/** The live contradiction is a CONDITION — it persists until resolved. */
const contradictionDelta = { elId: 'el-q1a1', kind: 'condition' as const, label: 'contradiction — R12 opposes R9 (unresolved; hold-out running)' }

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
    'Month 4, back after 8 days away. Past a few days, re-entry is a BRIEFING, not a diff: ' +
    'authored prose, ranked by consequence, every paragraph a door — and it flags what it ' +
    'could NOT resolve. The absence policy beneath it: numbers stayed current, structure ' +
    'held, timers paused. To pick up the winter thread you start work from where you stand:',
  advance: { on: 'work:q1', hint: 'click  ▷ work  on the first question to open a sitting' },
  world: {
    ...mBase,
    briefing: {
      away: '8 days',
      paras: [
        { text: 'The standing condition is the winter contradiction: R12’s winter refit opposes R9’s full-year slope, and the 2025 hold-out that will arbitrate is running on hpc right now.', elId: 'el-q1a1' },
        { text: 'The drift claim advanced to supported. Batch 7 arrived and passed QC (the usual three sensors flagged) — but its upstream source changed, so a drift flag stands until the next sweep.', elId: 'el-q1p2' },
        { text: 'Your committed direction moved while you were away: daily anomaly counts track the tidal coefficient (ρ = 0.61) — trail T2 now reads coherent across six weeks, and Q2’s plan is down to its last two analyses.', elId: 'el-q2' },
      ],
      flag: { text: 'One decision I could not make for you: the Q1 addendum (Jul 16) qualifies prose you ratified — it waits below, untouched.', elId: 'el-q1a1' },
      held: 'while you were away: numbers and figures stayed current · structure held — nothing moved · 2 decisions waited (their timers paused)',
    },
    sediment: matureSed,
    desk: {
      line: 'no open sessions',
      items: [{ label: 'last session: “anomaly cluster dig”', meta: 'Jul 18 · under Q2 · 1 run', action: 'transcript ▷', sessionId: 'anomaly cluster dig' }],
    },
    sessions: [anomalyDig],
    deltas: [contradictionDelta],
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
        { ts: 'Jul 20', text: 'winter flip is NOT a service artifact — refit excluding serviced stations, 31/48 stations flip (session: winter dig)', elId: 'el-q1a2' },
        { ts: 'Jul 20', text: 'addendum drafted for Q1 — awaiting ratification', elId: 'el-q1a2' },
        { ts: 'today', text: 'hold-out check on 2025 data — still running on hpc', live: true, elId: 'el-run_holdout' },
      ],
    },
    deltas: [contradictionDelta],
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
// changes MATERIALIZE where they land (best seen, not suppressed) while
// the viewport scroll-anchors on a visible landmark — the page never
// jumps; out-of-view changes pulse the TOC and tick the delta rail (3
// tiers); deixis is mutual (click the page → "looking at"; message refs
// locate their element); cross-boundary relevance stays a proposal;
// hold ⌖ parks an excerpt on the desk.
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
    'changes IN view materialize where they land — best seen, not suppressed — while the ' +
    'viewport holds a visible landmark steady (scroll-anchoring; the page never jumps). ' +
    'Changes OUT of view pulse the TOC and tick the delta rail (teal accretion · amber ' +
    'awaiting-you · red condition). Deixis is mutual: click any figure or trail and the ' +
    'panel’s “looking at:” follows; the agent points back (“show T1 →”). The Q2 relevance ' +
    'stays a PROPOSAL; hold ⌖ parks the addendum on the desk.',
  world: {
    ...mBase,
    whatsNew: {
      since: 'Jul 12',
      items: [
        { ts: 'now', text: 'hold-out finished — winter flip CONFIRMED on 2025 data (−0.31 ± 0.09) → the contradiction resolves', loud: true, elId: 'el-q1a1' },
        ...mWhatsNew.items.filter(i => !i.live),
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

// ---- M8 · year 2 — the scale face + the busy-scientist surfaces
// 8 questions, 4 trails, 214 runs: dormant questions compact to ONE line
// each (holding their claims); stalled trails fold; the sediment shows its
// recent window; the triage band still answers the whole visit in one
// glance, the tray clears the queue without hunting, ⌘K asks or finds from
// anywhere, and "this week ▸" renders the PI's emailable digest.
const dormantQs: Section[] = [
  { id: 'q3', question: 'Is sensor drift reversible after re-calibration?', phase: 'late', paragraphs: [], addenda: [], dormant: { since: 'Feb', holds: 'Re-calibration restores baseline within 0.5% (robust)' } },
  { id: 'q4', question: 'Do storm events bias the anomaly detector?', phase: 'mid', paragraphs: [], addenda: [], dormant: { since: 'Apr', holds: 'Detector unbiased once storm days are excluded (cross-checked)' } },
  { id: 'q5', question: 'Can batch 3’s gap be imputed?', phase: 'late', paragraphs: [], addenda: [], dormant: { since: 'Jan', holds: 'Imputation viable for gaps under 6 h (supported)' } },
  { id: 'q6', question: 'Salinity cross-sensitivity of the pressure channel?', phase: 'mid', paragraphs: [], addenda: [], dormant: { since: 'Mar', holds: 'No detectable cross-sensitivity (supported)' } },
  { id: 'q7', question: 'Do mooring depths shift after storms?', phase: 'early', paragraphs: [], addenda: [], dormant: { since: 'May' } },
  { id: 'q8', question: 'Inter-annual comparability — 2024 vs 2025', phase: 'early', paragraphs: [], addenda: [], dormant: { since: 'Jun', holds: 'Comparable after drift correction (conjecture)' } },
]
const stalledTrails: Trail[] = [
  {
    id: 'T3', title: 'Heavier tails after firmware 2.1', state: 'stalled',
    fragments: [
      { ts: 'Feb 11', text: 'Post-update response distributions look heavier-tailed on 6 sensors.' },
      { ts: 'Mar 02', text: 'Vendor notes mention a filter change in 2.1 — plausible mechanism, unverified.' },
    ],
  },
  {
    id: 'T4', title: 'North-shore sensors age faster', state: 'stalled',
    fragments: [
      { ts: 'Jan 20', text: 'Gain decline slope roughly 2× south-shore units.' },
      { ts: 'Feb 28', text: 'Could be exposure (fetch) — no covariate data yet.' },
      { ts: 'Apr 06', text: 'Two more units replaced on the north line; effect persists in the survivors.' },
    ],
  },
]
const m8: Scene = {
  id: 'm8', group: 'mature', title: 'year 2 — scale',
  narration:
    'Year 2: 8 questions, 4 trails, 214 runs — the SCALE face. Dormant questions ' +
    'compact to one quiet line each, holding their claims; stalled trails fold; the ' +
    'sediment shows its recent window. The triage band still answers the visit in one ' +
    'glance — ⚡ condition, ▢ needs-you (open the tray: ratify, batch-file routine, or ' +
    'go), ▷ resume. Try ⌘K (“did the hold-out land?”), a what’s-new line as a door, ' +
    'and “this week ▸” — the emailable digest.',
  world: {
    ...m5.world,
    sections: [...m5.world.sections, ...dormantQs],
    trails: [...m5.world.trails.map(t => t.id !== 'T1' ? t : {
      ...t,
      fragments: t.fragments.map((f, i) => i === t.fragments.length - 1 ? { ...f, draft: true } : f),
    }), ...stalledTrails],
    sedimentTotal: 214,
    deltas: [contradictionDelta],
    // the trust ratchet: ceremony is EARNED AWAY — after a run of accepts
    // the system proposes lowering its own ceremony, visibly and reversibly
    ratchet: { text: 'you’ve accepted the last 31 number refreshes without changes — stop showing them individually? they still land in the briefing and the ledger.' },
  },
}

// =======================================================================
// PART III — VERY MATURE (year 3). One paper's project, honestly counted:
// a Science-scale paper is 4–6 main + 30–50 supplementary figures, times
// the 5–10× that never leaves the lab (negative results, alternative
// attempts) — hundreds of figure-grade artifacts, 15–30 investigation
// lines over the project's lifetime. A flat scroll holds ~5–8 LIVE
// narrative lines: one question's active working set, a tenth of the
// project. So the Record RECURSES: the spine (project grain — rolling
// abstract over arcs, every question one line) with the full notebook
// face living one level down, per question. Compaction is the common
// case: mostly one-liners, three chapters open.
// =======================================================================

const sitePairing: SessionRec = {
  id: 'site pairing', label: 'site pairing', when: 'May 10', state: 'open',
  anchor: { kind: 'question', label: 'Q3.1 · exposed vs sheltered' },
  scope: [{ kind: 'question', label: 'Q3.1 · exposed vs sheltered' }],
  turns: 3,
  msgs: [
    { role: 'you', text: 'Rerun the site-pair contrast with the estuary pair excluded — is the third pair confounded or real?' },
    { role: 'guide', text: 'Pairs A and B hold (+0.21, +0.18); pair C flips sign when the estuary stations drop. Queued the hold-out on the 2027 winter subset to arbitrate.' },
    { run: { title: 'Site-pair hold-out — estuary excluded', state: 'running', meta: 'hpc · started 09:12' }, role: 'system' },
  ],
  distillate: [], leftovers: [],
}
const winterDig2: SessionRec = {
  id: 'winter dig II', label: 'winter dig II', when: 'May 7', state: 'filed',
  anchor: { kind: 'question', label: 'Q2.2 · winter anomaly' },
  scope: [{ kind: 'question', label: 'Q2.2 · winter anomaly' }],
  turns: 6,
  msgs: [
    { role: 'you', text: 'Final panels for the winter addendum — the 2027 recurrence and the tidal phase alignment.' },
    { role: 'guide', text: 'Rendered both. The recurrence stands at −0.31 ± 0.09; the addendum draft is updated and awaits your ratification.' },
  ],
  distillate: [{ text: 'Addendum — the winter anomaly recurs in 2027; tidal forcing favored', dest: 'Q2.2 · awaiting ratification' }],
  leftovers: [{ id: 'fig_qc_ok1', title: 'Phase-alignment residuals by station (unexamined)' }],
}

/** Year-3 sediment: the RECENT WINDOW only — 1,847 runs live in the
 *  archive, searchable; the page shows this week's pulse. */
const y3Sed: SedimentEntry[] = [
  {
    id: 'y3_holdout', date: 'May 10', title: 'Site-pair hold-out — estuary excluded',
    state: 'running', verdict: 'running on hpc — 2027 winter subset', nOutputs: 0, shown: [],
    retention: 'temporary', site: 'hpc', isNew: true, sessionRef: 'site pairing', turnRef: 3,
  },
  {
    id: 'y3_pairs', date: 'May 9', title: 'Site-pair refit — pairs A/B/C',
    state: 'ok', verdict: '2 of 3 pairs hold (A +0.21, B +0.18); C confounded by the estuary',
    nOutputs: 6, shown: [], retention: 'kept', site: 'hpc', isNew: true, sessionRef: 'site pairing', turnRef: 2,
  },
  {
    id: 'y3_krig', date: 'May 8', title: 'Kriging cross-validation — 2027 field',
    state: 'ok', verdict: 'residuals non-stationary — R190 opposes R171’s stationarity assumption',
    nOutputs: 4, shown: [], retention: 'kept', site: 'hpc',
  },
  {
    id: 'y3_qc', date: 'May 8', title: 'Weekly QC sweep — all stations',
    state: 'ok', verdict: 'acceptable — 0 flagged', nOutputs: 96, shown: [], retention: 'temporary',
  },
  {
    id: 'y3_panels', date: 'May 7', title: 'Winter addendum — final panels',
    state: 'ok', verdict: 'recurrence −0.31 ± 0.09 · phase alignment holds',
    nOutputs: 3, shown: [], retention: 'kept', sessionRef: 'winter dig II', turnRef: 2,
  },
]

const y3Spine: Spine = {
  abstract: [
    { text: 'Coastal sensor networks carry a seasonal bias that is correctable but spatially structured. The winter drift at exposed sites (−0.8 °C) is instrumental: correction model C2 restores year-round comparability, and the calibration arc is closed ([[arc:A1]]).' },
    { text: 'The residual winter anomaly is real. It survives correction, recurs in the 2027 data (−0.31 ± 0.09), and aligns with tidal phase; tidal forcing is favored over vertical mixing. This is the paper’s central claim — the addendum is drafted and awaits ratification ([[arc:A2]]).' },
    { text: 'Spatial structure is the open front ([[arc:A3]]): exposed/sheltered pairing holds at two of three site pairs, and interpolating the bias field is blocked — the [[run:y3_krig|kriging cross-validation]] shows non-stationary residuals.' },
  ],
  synthesisNote: 'rolling synthesis · drafted by Guide · re-ratified by you · May 4, 2028',
  superseded: {
    label: 'supersedes the Nov 2027 synthesis (archived)',
    note: 'Nov 2027 synthesis — archived, immutable, still cited from A2’s narrative. Consolidation never rewrites: each synthesis is a new layer over the last; the full long-form story lives on each question’s page.',
  },
  sessionsTotal: 212,
  arcs: [
    {
      id: 'A1', title: 'Calibration & drift', era: 'y1 · closed', runs: 388,
      holds: 'Winter drift is instrumental and correctable — model C2 restores year-round comparability (robust)',
      questions: [
        { id: 'q11', title: 'Is the calibration stable across seasons?', state: 'closed', holds: 'No — winter drift −0.8 °C at exposed sites; correction model C2 adopted (robust)' },
        { id: 'q12', title: 'Does sensor age predict drift rate?', state: 'closed', holds: 'Weakly (r = 0.31) — age dropped from the correction model (cross-checked)' },
        { id: 'q13', title: 'Drift is a firmware artifact', state: 'dead', epitaph: { verdict: 'ruled out — cross-vendor replication shows identical drift', run: 'R41', date: 'Feb y1' } },
        { id: 'q14', title: 'Salinity fouling explains exposed-site drift', state: 'dead', epitaph: { verdict: 'ruled out — fouling scrub changed nothing', run: 'R57', date: 'May y1' } },
      ],
    },
    {
      id: 'A2', title: 'The seasonal signal', era: 'y1–y2', open: true, runs: 611,
      questions: [
        { id: 'q21', title: 'Is the summer amplitude real or aliasing?', state: 'closed', holds: 'Real — confirmed at 3 sites with 10-minute sampling (robust)' },
        {
          id: 'q22', title: 'Does the winter anomaly recur, and what drives it?', state: 'open',
          now: 'Recurs in the 2027 data (−0.31 ± 0.09); tidal forcing favored over vertical mixing — the paper’s central claim. Addendum drafted, awaiting you.',
          session: { label: 'winter dig II' }, activity: 'May 7',
          pending: [{ key: 'q22-add', kind: 'addendum', label: 'addendum · 2027 recurrence confirmed — ratify?', routine: false }],
        },
        { id: 'q23', title: 'Storm events as regime markers', state: 'held', holds: '3 candidate events tagged (conjecture)', since: 'Nov y2' },
        { id: 'q24', title: 'The anomaly tracks the lunar cycle', state: 'dead', epitaph: { verdict: 'ruled out — phase scramble kills the correlation', run: 'R102', date: 'Nov y2' } },
      ],
    },
    {
      id: 'A3', title: 'Spatial structure of the bias', era: 'y2–y3 · active', open: true, runs: 402,
      questions: [
        {
          id: 'q31', title: 'Do exposed and sheltered sites differ?', state: 'open',
          now: '2 of 3 site pairs hold (+0.21, +0.18); the third is confounded by the estuary — hold-out running to arbitrate.',
          session: { label: 'site pairing', live: true }, activity: 'today · 2 runs',
          pending: [{ key: 'q31-frag', kind: 'fragment', label: 'fragment · “pair C flips when estuary stations drop” — file?', routine: true }],
        },
        {
          id: 'q32', title: 'Can we interpolate the bias field between sites?', state: 'open',
          now: 'Blocked — kriging residuals are non-stationary (R190 opposes R171’s stationarity assumption).',
        },
        { id: 'q33', title: 'Depth stratification of the bias', state: 'held', since: 'Mar y3' },
      ],
    },
    {
      id: 'A4', title: 'Methods & harmonization', era: 'cross-cutting', runs: 446,
      holds: 'STL detrending adopted throughout; NOAA harmonization parked feasible',
      questions: [
        { id: 'q41', title: 'Detrending: STL or polynomial?', state: 'closed', holds: 'STL — beats polynomial on winter residuals; adopted throughout (robust)' },
        { id: 'q42', title: 'Cross-network harmonization with NOAA buoys', state: 'held', holds: 'Feasible on overlapping months (conjecture)', since: 'Jan y3' },
        { id: 'q43', title: 'Neural gap-filling beats linear interpolation', state: 'dead', epitaph: { verdict: 'ruled out — worse at gaps over 6 h', run: 'R148', date: 'Jan y3' } },
      ],
    },
  ],
}

const m9: Scene = {
  id: 'm9', group: 'late', title: 'year 3 — the spine',
  narration:
    'Year 3: 12 questions across 4 arcs · 1,847 runs · 212 sessions — an order of magnitude past ' +
    'one scroll, so the Record RECURSES. The spine is the project page: a rolling ratified ' +
    'abstract (consolidation supersedes, never rewrites — the Nov synthesis is archived beneath), ' +
    'then every question as ONE line whose face follows its state: open · held · closed · dead. ' +
    'Dead lines are EPITAPHS — hypothesis, verdict, the run that killed it; the paper reports the ' +
    'survivors, the record keeps the casualties (⌘K “gap-filling”). Closed arcs fold whole; the ' +
    'periphery rolls up per arc; the triage band is unchanged — it was always derived, never ' +
    'positional. And structural change arrives BATCHED: one proposal, one sitting — held across ' +
    'cycles before it was raised, priced in reader-visible terms, the rejected alternative shown; ' +
    '“never” writes a rule.',
  advance: { on: 'descend:q22', hint: 'click  open ▸  on the winter-anomaly question (A2) to descend to its page' },
  world: {
    project: { title: 'Coastal sensor study', started: '2026-03-02', lastVisit: '2028-05-03' },
    ...emptyWorld,
    onePager: coastalWorld.onePager,
    spine: y3Spine,
    whatsNew: {
      since: 'May 3',
      items: [
        { ts: 'May 7', text: 'winter addendum drafted — 2027 recurrence confirmed (−0.31 ± 0.09)', elId: 'el-q22' },
        { ts: 'May 8', text: 'kriging cross-validation fails — residuals non-stationary (condition raised)', loud: true, elId: 'el-q32' },
        { ts: 'May 9', text: 'site pairs: 2 of 3 hold; pair C confounded by the estuary', elId: 'el-q31' },
        { ts: 'today', text: 'site-pair hold-out running on hpc — 2027 winter subset', live: true, elId: 'el-y3_holdout' },
      ],
    },
    sediment: y3Sed,
    sedimentTotal: 1847,
    sessions: [sitePairing, winterDig2],
    desk: {
      line: '1 open session',
      items: [{ label: 'Q3.1 · site pairing', meta: 'started 09:10 · 1 run in flight', live: true, sessionId: 'site pairing' }],
    },
    deltas: [
      { elId: 'el-q32', kind: 'condition', label: 'interpolation blocked — kriging residuals non-stationary (R190 vs R171)' },
      { elId: 'el-sediment', kind: 'accretion', count: 5, label: '5 runs this week' },
    ],
    // structural change arrives BATCHED — one proposal, one sitting;
    // hysteresis, not weather. Each item priced in reader-visible units,
    // the rejected alternative shown, "never" writes a rule.
    rfc: {
      title: 'restructuring proposal — May 12',
      note: 'preference held for 3 consecutive weekly evaluations · priced in reader-visible terms · structural budget this cycle: 2 items',
      items: [
        {
          verb: 'fold', what: 'A4 · Methods & harmonization → archive rank', cls: 2,
          expires: 'applies May 26 unless vetoed · timer pauses while you’re away',
          why: 'every line closed or held; no new inbound link in 6 months (last: R148, Jan)',
          impact: 'one holds-line replaces three rows · nothing is reworded — the fold SELECTS the abstract rendition you ratified in Jan · every link keeps resolving',
          alt: 'leave it open — rejected: its only motion in half a year is the NOAA question going dormant',
        },
        {
          verb: 'split', what: 'A3 · Spatial structure → “site pairing” + “field interpolation”', cls: 3,
          why: 'two live directions under one arc: pairing runs today (▶) while interpolation is BLOCKED on non-stationarity (R190 vs R171) — one amber roll-up now mixes unrelated states',
          impact: '2 arcs · 3 questions re-home · no prose moves and no page changes — addressing is by entity, nothing 404s',
          alt: 'sub-headers within A3 — rejected: the periphery cannot roll up half an arc',
        },
      ],
    },
  },
}

// ---- M10 · descend: the question page IS the earlier prototype
const m10: Scene = {
  id: 'm10', group: 'late', title: 'descend — one question',
  narration:
    'One level down, and the WHOLE earlier prototype is here: the question page IS the notebook ' +
    'face you have been watching all along — its narrative, its trails, its sediment slice, its ' +
    'sessions. Nothing was redesigned to scale: the single-scroll Record was the QUESTION-grain ' +
    'face all along, and a young project (E1–E5) is simply one that has not needed its spine ' +
    'yet. ‹ climbs back up.',
  world: {
    ...m5.world,
    project: { ...m5.world.project, title: 'Does the winter anomaly recur, and what drives it?' },
    crumb: { up: 'Coastal sensor study', arc: 'A2 · the seasonal signal' },
    // ONE question's page: the two narrative sections read as its sub-lines
    sections: m5.world.sections.map(s =>
      s.id === 'q1' ? { ...s, question: 'Does it recur? — the winter refits' }
      : s.id === 'q2' ? { ...s, question: 'What drives it? — tides vs the estuary' }
      : s),
  },
}

export const SCENES: Scene[] = [e1, e2, e3, e4, e5, e6, m1, m2, m3, m4, m5, m6, m7, m8, m9, m10]

export const GROUPS: { id: 'early' | 'mature' | 'late'; label: string }[] = [
  { id: 'early', label: 'I · early days (day 0–4)' },
  { id: 'mature', label: 'II · mature project (month 4)' },
  { id: 'late', label: 'III · very mature (year 3)' },
]
