/**
 * The Record — fixture projection for the living-notebook prototype.
 * Same generic demo world as altui1 (the "Coastal sensor study"), rendered
 * as the three strata of alt_uis.md §2: ratified narrative on top, co-written
 * field notes / trails beneath, automatic sediment at the bottom.
 *
 * Inline reference grammar used in prose (every rendered fact is a live view
 * over an entity, never pasted text):
 *   [[fig:ID|label]]     — inline figure reference chip (opens disclosure)
 *   [[claim:ID]]         — claim chip with live maturity
 *   [[run:ID|label]]     — run reference
 *   [[trail:ID]]         — trail reference
 *   [[figure:ID]]        — block-level figure embed (own line)
 */

export interface ClaimRef {
  title: string
  maturity: 'conjecture' | 'supported' | 'cross-checked' | 'robust' | 'contested'
  evidence: number
  caveats: string[]
}

export interface Paragraph {
  id: string
  text: string
  ratified: { by: string; on: string; draftedBy?: string }
}
export interface Addendum {
  id: string
  on: string
  text: string
  status: 'pending' | 'ratified'
}
export interface Section {
  id: string
  question: string
  phase: 'early' | 'mid' | 'late'
  paragraphs: Paragraph[]
  addenda: Addendum[]
  /** open questions — rendered on the stub face when no prose is ratified yet */
  open?: string[]
  /** archived working sessions filed under this question */
  sessions?: { label: string; when: string; meta: string }[]
  /** scale face: a dormant question compacts to ONE quiet line — question,
   *  what it holds, since when — with a ▷ wake door. No dead scaffolding. */
  dormant?: { since: string; holds?: string }
  /** a PROACTIVE importance marker — the scientist's commitment, declared
   *  BEFORE the evidence exists. The floor converts, never refuses: prose
   *  tracks evidence, structure tracks intent. */
  intent?: { on: string }
  /** the future tense: planned analyses forming the section's draft plan.
   *  Each item is a door and a launcher; the skeleton is ratified as a
   *  SHAPE, then filled by work at lowered ceremony. The list itself is
   *  the scientist's most direct control surface — their own additions,
   *  edits, and parkings carry NO ceremony (the propose→ratify gate
   *  exists for the agent's writes, not the user's intent). */
  plan?: { text: string; state: 'planned' | 'taken-up' | 'produced' | 'absorbed'; meta?: string; mine?: boolean }[]
  /** the skeleton is still a proposal — awaiting "ratify the shape" */
  planDraft?: boolean
  /** the section's governing metadata, edited IN PLACE (the spine is the
   *  map — there is no separate plan pane) */
  charge?: string
  budget?: string
  authored?: string
  pinned?: boolean
}

export interface Fragment {
  ts: string
  text: string
  ref?: string        // figure id the fragment points at
  counter?: boolean   // a counter-example — trails keep those too
  draft?: boolean     // proposed by the agent during a session, not yet ratified
  /** the exchange that drafted this — provenance for prose, at turn grain */
  src?: { sess: string; turn: number }
}
export interface Trail {
  id: string
  title: string
  state: 'accumulating' | 'cohering' | 'stalled'
  fragments: Fragment[]
  nudge?: { text: string; action: string }
}

export interface LooseNote {
  id: string
  ts: string
  origin: 'you' | 'guide'
  text: string
  ref?: string
  draft?: boolean
}

export interface SedimentOutput { id: string; kind: 'figure' | 'table'; title: string; flagged?: boolean }
export interface SedimentEntry {
  id: string
  date: string
  title: string
  state: 'ok' | 'running' | 'failed'
  verdict: string
  nOutputs: number
  shown: SedimentOutput[]      // the outputs worth a thumbnail (flagged/kept)
  retention: 'kept' | 'temporary' | 'at-risk'
  site?: string
  trailRef?: string
  /** id of the working session that produced this run (▷/▶ chip) */
  sessionRef?: string
  /** the turn within that session that launched/reported this run */
  turnRef?: number
  /** landed during the session on screen — highlighted as just-arrived */
  isNew?: boolean
}

export interface Prov {
  runTitle: string
  date: string
  placement: string
  code: string
  params: Record<string, string | number>
  env: { packages: string[]; fingerprint: string }
  log: string
  inputs: { id: string; title: string }[]
}

export interface BenchMsg { role: 'you' | 'guide'; text: string }

export const project = {
  title: 'Coastal sensor study',
  started: '2026-03-02',
  lastVisit: '2026-07-12',
}

export const whatsNew = {
  since: 'Jul 12',
  items: [
    { ts: 'Jul 15', text: 'claim advanced — “Sensor drift is temperature-driven” → supported' },
    { ts: 'Jul 16', text: 'contradiction — R12 opposes R9 (opposite sign, winter subset)', loud: true },
    { ts: 'Jul 17', text: 'QC sweep on batch 7 — 104 outputs, verdict acceptable, 3 flagged' },
    { ts: 'Jul 17', text: 'batch 7 upstream source changed (drift flag raised)' },
    { ts: 'Jul 18', text: 'tidal coefficient join — anomaly rate tracks tides (ρ = 0.61)' },
    { ts: 'today', text: 'hold-out check on 2025 data started on hpc — running now', live: true },
  ] as { ts: string; text: string; loud?: boolean; live?: boolean }[],
}

/** Drafts awaiting ratification — the honest "record vs work" gap. */
export const pendingDrafts = 3

export const claims: Record<string, ClaimRef> = {
  clm_tempdrift: {
    title: 'Sensor drift is temperature-driven',
    maturity: 'supported', evidence: 2,
    caveats: ['single method so far (linear fit)', 'winter subset contradicts — hold-out in flight'],
  },
  clm_calsummer: {
    title: 'Calibration is stable in summer months only',
    maturity: 'conjecture', evidence: 1,
    caveats: ['needs a second method'],
  },
}

export const sections: Section[] = [
  {
    id: 'q1',
    question: 'Is the calibration stable across seasons?',
    phase: 'mid',
    charge: 'Establish whether year-round comparability is achievable, and under what correction — the grant’s central promise. Keep the winter mechanism open until the hold-out arbitrates; frame nothing as thermal-only before it lands.',
    budget: '~1,600 w · actual 430 w',
    authored: 'drafted by Guide · 100% ratified by you',
    paragraphs: [
      {
        id: 'q1p1',
        text: 'Across batches 1–6 the calibration holds in the warm months: once the seasonal component is removed, summer gain varies by less than 2% between batches ([[fig:fig_seasonal|detrended series]]). We currently hold this as [[claim:clm_calsummer]].',
        ratified: { by: 'you', on: 'Jul 14', draftedBy: 'Guide' },
      },
      {
        id: 'q1p2',
        text: 'The full-year picture is genuinely mixed. Drift correlates with temperature over the whole record (slope +0.45 ± 0.07, [[fig:fig_calfit|R9]]), which supports [[claim:clm_tempdrift]] — but the evidence is not yet method-diverse, and the winter story below cuts against a purely thermal mechanism. Evidence is mixed; this section will stay hedged until the hold-out resolves it.',
        ratified: { by: 'you', on: 'Jul 15', draftedBy: 'Guide' },
      },
    ],
    addenda: [
      {
        id: 'q1a1',
        on: 'Jul 16',
        text: 'Evidence since this was written cuts against it: the winter-only refit flips the sign (−0.32 ± 0.11, [[fig:fig_winter|R12]]) — opposite to R9. Either the mechanism is not purely temperature, or the January service visits confound the winter subset ([[trail:T1]]). A hold-out check on 2025 data is [[run:run_holdout|running now]].',
        status: 'pending',
      },
    ],
  },
  {
    id: 'q2',
    question: 'What drives the anomaly cluster?',
    phase: 'early',
    intent: { on: 'Mar 04' },
    charge: 'Characterize the anomaly cluster and its driver candidates. A committed direction, marked ahead of the evidence — prose stays behind the evidence; the plan carries the shape.',
    budget: '~1,200 w · actual 90 w',
    authored: 'drafted by Guide · 100% ratified by you',
    plan: [
      { text: 'Spatial distribution of anomaly events against the coastline', state: 'absorbed', meta: '→ fig, in prose' },
      { text: 'Tide / weather covariate join on daily counts', state: 'absorbed', meta: '→ ρ = 0.61, in prose' },
      { text: 'Estuary-distance gradient — does amplitude decay with distance?', state: 'planned' },
      { text: 'Detector-bias check on storm days', state: 'planned' },
    ],
    paragraphs: [
      {
        id: 'q2p1',
        text: '26 of 31 anomaly events fall within 4 km of the estuary mouth ([[figure:fig_anommap]]) and daily counts track the tidal coefficient (ρ = 0.61, [[fig:fig_springtide|tidal join]]). Nothing here is claimed yet — the pattern is NOTICED, and accumulating on [[trail:T2]].',
        ratified: { by: 'you', on: 'Jul 18', draftedBy: 'Guide' },
      },
    ],
    addenda: [],
  },
]

export const trails: Trail[] = [
  {
    id: 'T1',
    title: 'Something is off in the seasonal component',
    state: 'accumulating',
    fragments: [
      { ts: 'Jun 12', text: 'Winter panels of the decomposition are noisier than shot noise alone would predict.', ref: 'fig_seasonal' },
      { ts: 'Jul 08', text: 'Service notes: stations 8–14 serviced in January — gain jumps may align with visits, not weather.' },
      { ts: 'Jul 14', text: 'Detrending kills the summer variance but winter residuals stay structured.', ref: 'fig_calcurve' },
      { ts: 'Jul 16', text: 'Counter-example: station B11 (never serviced) still shows the winter flip — weakens the service-artifact reading.', ref: 'fig_winter', counter: true },
    ],
  },
  {
    id: 'T2',
    title: 'Estuary cluster follows the tides',
    state: 'cohering',
    fragments: [
      { ts: 'Jul 05', text: 'Anomaly events cluster near the estuary mouth (26/31).', ref: 'fig_anommap' },
      { ts: 'Jul 15', text: 'Tide-gauge feed shows a clean spring–neap envelope — joinable to the event times.', ref: 'fig_tide' },
      { ts: 'Jul 18', text: 'Daily anomaly counts track the tidal coefficient (ρ = 0.61).', ref: 'fig_springtide' },
    ],
    nudge: {
      text: 'These three fragments are mutually consistent across six weeks — draft a claim?',
      action: 'Draft: “Anomaly rate rises with spring tides”',
    },
  },
]

export const looseNotes: LooseNote[] = [
  {
    id: 'n1', ts: 'Jul 17', origin: 'guide',
    text: 'Sensor 14 response is bimodal in batch 7 — the lower mode sits exactly at the pre-service calibration value. Flagged in QC.',
    ref: 'fig_qc_flag1',
  },
  {
    id: 'n2', ts: 'Jul 17', origin: 'guide',
    text: 'Sensor 31 drops out in 6.8% of intervals, always the same 40-minute window after midnight — looks like a logger duty cycle, not weather.',
    ref: 'fig_qc_flag3',
  },
  {
    id: 'n3', ts: 'Jul 03', origin: 'you',
    text: 'Midnight-aligned pressure spikes on 3 channels — amplitudes quantized, probably logger rollover. Parked.',
  },
]

export const sediment: SedimentEntry[] = [
  {
    id: 'run_holdout', date: 'Jul 19', title: 'Hold-out check — 2025 data',
    state: 'running', verdict: 'running on hpc — fold 3/5', nOutputs: 0, shown: [],
    retention: 'temporary', site: 'hpc',
  },
  {
    id: 'run_springtide', date: 'Jul 18', title: 'Tidal coefficient join',
    state: 'ok', verdict: 'anomaly rate tracks tidal coefficient (ρ = 0.61)', nOutputs: 3,
    shown: [{ id: 'fig_springtide', kind: 'figure', title: 'Anomaly rate vs tidal coefficient' }],
    retention: 'kept', site: 'hpc', trailRef: 'T2',
  },
  {
    id: 'run_qc', date: 'Jul 17', title: 'QC sweep — batch 7 intake',
    state: 'ok', verdict: 'acceptable — 3 sensors flagged (14, 22, 31)', nOutputs: 104,
    shown: [
      { id: 'fig_qc_flag1', kind: 'figure', title: 'Sensor 14 — bimodal (flagged)', flagged: true },
      { id: 'fig_qc_flag2', kind: 'figure', title: 'Sensor 22 — heavy tail (flagged)', flagged: true },
      { id: 'fig_qc_flag3', kind: 'figure', title: 'Sensor 31 — dropout gaps (flagged)', flagged: true },
      { id: 'fig_qc_ok3', kind: 'figure', title: 'Batch 7 — completeness by day' },
    ],
    retention: 'at-risk', site: 'hpc', trailRef: 'T1',
  },
  {
    id: 'run_winter', date: 'Jul 16', title: 'Winter subset re-fit',
    state: 'ok', verdict: 'slope −0.32 ± 0.11 — OPPOSITE sign vs full year (R9 ⚡)', nOutputs: 3,
    shown: [{ id: 'fig_winter', kind: 'figure', title: 'Drift vs temperature — winter (R12)' }],
    retention: 'kept', site: 'hpc',
  },
  {
    id: 'run_tide', date: 'Jul 15', title: 'Tide-gauge feed — first look',
    state: 'ok', verdict: 'clean series; spring–neap envelope visible', nOutputs: 2,
    shown: [{ id: 'fig_tide', kind: 'figure', title: 'Tide-gauge feed — first 30 days' }],
    retention: 'temporary',
  },
  {
    id: 'run_seasonal', date: 'Jul 14', title: 'Seasonal decomposition — batches 1–6',
    state: 'ok', verdict: 'stable summer gain; winter panels noisy', nOutputs: 15,
    shown: [
      { id: 'fig_seasonal', kind: 'figure', title: 'Detrended series' },
      { id: 'fig_calcurve', kind: 'figure', title: 'Calibration curve by season' },
    ],
    retention: 'kept', site: 'hpc',
  },
  {
    id: 'run_anom', date: 'Jul 05', title: 'Anomaly cluster extraction',
    state: 'ok', verdict: '31 events; 26 within 4 km of estuary mouth', nOutputs: 6,
    shown: [{ id: 'fig_anommap', kind: 'figure', title: 'Anomaly events — spatial' }],
    retention: 'kept', site: 'hpc', trailRef: 'T2',
  },
  {
    id: 'run_pressure', date: 'Jul 03', title: 'Pressure spike check',
    state: 'ok', verdict: 'midnight spikes are quantized — logger artifact, parked', nOutputs: 4,
    shown: [],
    retention: 'temporary',
  },
  {
    id: 'run_calfit', date: 'Jun 28', title: 'Calibration drift fit — full year',
    state: 'ok', verdict: 'slope +0.45 ± 0.07 (p = 3.1e-8)', nOutputs: 5,
    shown: [{ id: 'fig_calfit', kind: 'figure', title: 'Drift vs temperature — full year (R9)' }],
    retention: 'kept', site: 'hpc',
  },
  {
    id: 'run_failed_join', date: 'Jun 27', title: 'Weather join — first attempt',
    state: 'failed', verdict: '✗ station-id mismatch between feeds (fixed in the Jun 28 rerun)', nOutputs: 0,
    shown: [], retention: 'temporary',
  },
]

export const figureTitles: Record<string, string> = Object.fromEntries(
  sediment.flatMap(s => s.shown.map(o => [o.id, o.title])))

export const provenance: Record<string, Prov> = {
  fig_calfit: {
    runTitle: 'Calibration drift fit — full year', date: 'Jun 28 · 4 min', placement: 'ran on hpc · 2 GB',
    code: 'df = load("batches_1_6").join(load("weather"), on="station_day")\nfit = linfit(df.temperature, df.drift_mv)\nplot_fit(df, fit, out="calfit.png")\nprint(f"slope={fit.slope:+.2f} ± {fit.se:.2f} (p={fit.p:.1e})")',
    params: { window: 'full year', stations: 48, model: 'linfit' },
    env: { packages: ['pandas 2.2', 'statsmodels 0.15', 'numpy 2.1'], fingerprint: 'env:9f2a…c1 (locked)' },
    log: 'slope=+0.45 ± 0.07 (p=3.1e-08)\nn=4,812 station-days',
    inputs: [{ id: 'ds_b16', title: 'Sensor readings — batches 1–6' }, { id: 'ds_weather', title: 'Station weather reference' }],
  },
  fig_winter: {
    runTitle: 'Winter subset re-fit', date: 'Jul 16 · 2 min', placement: 'ran on hpc · 1 GB',
    code: 'w = df[df.month.isin([12,1,2])]\nfit = linfit(w.temperature, w.drift_mv)\nplot_fit(w, fit, out="winter.png")\nprint(f"slope={fit.slope:+.2f} ± {fit.se:.2f} (n={len(w)})")',
    params: { window: 'Dec–Feb', stations: 48, model: 'linfit' },
    env: { packages: ['pandas 2.2', 'statsmodels 0.15', 'numpy 2.1'], fingerprint: 'env:9f2a…c1 (locked)' },
    log: 'slope=-0.32 ± 0.11 (n=402)',
    inputs: [{ id: 'ds_b16', title: 'Sensor readings — batches 1–6' }],
  },
  fig_seasonal: {
    runTitle: 'Seasonal decomposition — batches 1–6', date: 'Jul 14 · 11 min', placement: 'ran on hpc · 6 GB',
    code: 'dec = stl_decompose(load("batches_1_6"), period="1y")\nplot_panels(dec, out="seasonal.png")\nexport(dec.detrended, "detrended.parquet")',
    params: { method: 'STL', period: '1y', robust: 'true' },
    env: { packages: ['pandas 2.2', 'statsmodels 0.15'], fingerprint: 'env:9f2a…c1 (locked)' },
    log: 'summer gain var: 1.9% across batches\nwinter residual structure: NOT white (LB p=0.003)',
    inputs: [{ id: 'ds_b16', title: 'Sensor readings — batches 1–6' }],
  },
  fig_anommap: {
    runTitle: 'Anomaly cluster extraction', date: 'Jul 05 · 3 min', placement: 'ran locally',
    code: 'ev = detect_anomalies(load("batches_1_6"), z=4)\nmap_events(ev, out="anommap.png")\nprint(len(ev), "events;", cluster_stats(ev))',
    params: { z: 4, min_gap: '6h' },
    env: { packages: ['pandas 2.2', 'scikit-learn 1.6'], fingerprint: 'env:9f2a…c1 (locked)' },
    log: '31 events; 26 within 4 km of estuary mouth',
    inputs: [{ id: 'ds_b16', title: 'Sensor readings — batches 1–6' }],
  },
  fig_springtide: {
    runTitle: 'Tidal coefficient join', date: 'Jul 18 · 2 min', placement: 'ran on hpc · 1 GB',
    code: 'tc = tidal_coefficient(load("tide_gauge"))\nrate = daily_counts(events).join(tc)\nscatter_fit(rate, out="springtide.png")\nprint("rho:", spearman(rate.coef, rate.n))',
    params: { join: 'daily', method: 'spearman' },
    env: { packages: ['pandas 2.2', 'scipy 1.14'], fingerprint: 'env:9f2a…c1 (locked)' },
    log: 'rho: 0.61 (p=0.002)',
    inputs: [{ id: 'ds_tide', title: 'Tide gauge series' }],
  },
  fig_tide: {
    runTitle: 'Tide-gauge feed — first look', date: 'Jul 15 · 1 min', placement: 'ran locally',
    code: 'plot_series(load("tide_gauge").head_days(30), out="tide.png")',
    params: { days: 30 },
    env: { packages: ['pandas 2.2'], fingerprint: 'env:9f2a…c1 (locked)' },
    log: 'ok',
    inputs: [{ id: 'ds_tide', title: 'Tide gauge series' }],
  },
  fig_qc_flag1: {
    runTitle: 'QC sweep — batch 7 intake', date: 'Jul 17 · 24 min', placement: 'ran on hpc · 12 GB',
    code: 'qc = qc_sweep(load("batch_7"))   # 104 outputs\nflagged = qc.flags()               # [14, 22, 31]\nreport(qc, out_dir="qc/")',
    params: { checks: 'completeness, response, gaps', sensors: 52 },
    env: { packages: ['pandas 2.2', 'qc-suite 0.9'], fingerprint: 'env:9f2a…c1 (locked)' },
    log: 'verdict: acceptable — 3 flagged\nsensor 14: bimodal response (KS 0.41)',
    inputs: [{ id: 'ds_b7', title: 'Sensor readings — batch 7' }],
  },
  fig_calcurve: {
    runTitle: 'Seasonal decomposition — batches 1–6', date: 'Jul 14 · 11 min', placement: 'ran on hpc · 6 GB',
    code: 'plot_calibration_by_season(dec, out="calcurve.png")',
    params: { method: 'STL', period: '1y' },
    env: { packages: ['pandas 2.2', 'statsmodels 0.15'], fingerprint: 'env:9f2a…c1 (locked)' },
    log: 'summer/winter slopes: 0.50 / 0.28',
    inputs: [{ id: 'ds_b16', title: 'Sensor readings — batches 1–6' }],
  },
  fig_qc_flag3: {
    runTitle: 'QC sweep — batch 7 intake', date: 'Jul 17 · 24 min', placement: 'ran on hpc · 12 GB',
    code: 'qc = qc_sweep(load("batch_7"))\nplot_gaps(qc, sensor=31)',
    params: { checks: 'gaps', sensor: 31 },
    env: { packages: ['pandas 2.2', 'qc-suite 0.9'], fingerprint: 'env:9f2a…c1 (locked)' },
    log: 'gap rate 6.8% — same 40-min window nightly',
    inputs: [{ id: 'ds_b7', title: 'Sensor readings — batch 7' }],
  },
  fig_qc_flag2: {
    runTitle: 'QC sweep — batch 7 intake', date: 'Jul 17 · 24 min', placement: 'ran on hpc · 12 GB',
    code: 'qc = qc_sweep(load("batch_7"))\nplot_response(qc, sensor=22)',
    params: { checks: 'response', sensor: 22 },
    env: { packages: ['pandas 2.2', 'qc-suite 0.9'], fingerprint: 'env:9f2a…c1 (locked)' },
    log: 'heavy tail (KS 0.36)',
    inputs: [{ id: 'ds_b7', title: 'Sensor readings — batch 7' }],
  },
  fig_qc_ok3: {
    runTitle: 'QC sweep — batch 7 intake', date: 'Jul 17 · 24 min', placement: 'ran on hpc · 12 GB',
    code: 'qc = qc_sweep(load("batch_7"))\nplot_completeness(qc)',
    params: { checks: 'completeness' },
    env: { packages: ['pandas 2.2', 'qc-suite 0.9'], fingerprint: 'env:9f2a…c1 (locked)' },
    log: 'median completeness 99.4%',
    inputs: [{ id: 'ds_b7', title: 'Sensor readings — batch 7' }],
  },
}

/** Canned margin-bench exchanges, keyed by element id. The margin opens ON an
 *  element with it already in focus — zero context-setting (the two-way
 *  context contract, repointed at the document). */
export const bench: Record<string, BenchMsg[]> = {
  fig_winter: [
    { role: 'you', text: 'Why would the sign flip only in winter?' },
    { role: 'guide', text: 'Three candidate mechanisms: (1) the thermal response is nonlinear and winter sits on the other side of the knee; (2) the January service visits re-zeroed part of the fleet mid-season (see trail T1 — but note the B11 counter-example); (3) a winter-only confounder like heater duty cycles. The running 2025 hold-out separates (1) from (2): a re-zeroing artifact won’t reproduce on unseen data.' },
  ],
  q1a1: [
    { role: 'you', text: 'Should I ratify this addendum now or wait for the hold-out?' },
    { role: 'guide', text: 'Ratify it — the addendum records that the contradiction EXISTS, which is already true, and it cites both fits. When the hold-out lands I will draft a second addendum resolving it either way; the ratified record stays honest at every point in time.' },
  ],
  T2: [
    { role: 'you', text: 'What would make this trail claim-ready?' },
    { role: 'guide', text: 'It nearly is: three consistent fragments across six weeks, two independent data sources. The weak spot is method diversity — everything flows through the same event detector. A rank-based re-detection (different z threshold, different window) agreeing would justify drafting the claim with “supported” rather than “conjecture”.' },
  ],
  fig_qc_flag1: [
    { role: 'you', text: 'Is the bimodality a hardware fault?' },
    { role: 'guide', text: 'The lower mode sits exactly at the pre-service calibration value, so a more likely reading is that sensor 14 intermittently reverts to its old calibration table — firmware, not analog. Cross-checking against the service log would settle it; want me to queue that?' },
  ],
}

export const benchFallback: BenchMsg[] = [
  { role: 'guide', text: 'This is the alt-UI prototype — the margin bench is canned here. In the real system this panel is the existing chat, opened with this element already focused (the same focus contract the workspace uses today).' },
]

/** The p-value visitor's one-pager (§2.4) — same machinery, thin project. */
export const onePager = {
  dataLine: '6 datasets · 2.5 GB · batch 7 flagged (source changed upstream)',
  methodLine: 'Linear fit of calibration drift vs station temperature, full year, 48 stations (pandas 2.2 / statsmodels 0.15, env locked).',
  number: 'slope = +0.45 ± 0.07 (p = 3.1×10⁻⁸)',
  caveat: 'Winter subset shows the opposite sign (R12) — treat the full-year figure as summer-dominated until the 2025 hold-out resolves it.',
}
