/** Live-World adapter guards: API World v1 → renderer World mapping, and a
 *  smoke render of the real Record component over an adapted world. Honest
 *  coverage: what the API lacks must come out EMPTY, not mimed. */
import { describe, it, expect, vi, afterEach, beforeAll } from 'vitest'
import { render, screen } from '@testing-library/react'
import Record from './Record'
import { apiToWorld, fetchLiveWorld, triageApi, worldUrl,
         type ApiWorld } from './live'

const LADDER = ['preliminary', 'supported', 'validated', 'contested', 'refuted']

function sample(): ApiWorld {
  return {
    version: 1,
    project_id: 'p-demo',
    roles: { question: 'thread', claim: 'claim' },
    maturity_ladder: LADDER,
    questions: [
      {
        id: 'Q1', title: 'Q1 drivers of variance',
        question: 'what drives variance across siteA runs?',
        open_questions: [{ text: 'batch effect?' }, 'calibration drift?'],
        lifecycle: 'open', claims: ['C1', 'C2'], prose: ['P1'],
      },
      {
        id: 'Q2', title: 'Q2 parked line', question: null,
        open_questions: [], lifecycle: 'parked', claims: [], prose: [],
        updated_at: '2026-03-04T12:00:00Z',
      },
    ],
    claims: [
      { id: 'C1', title: 'variance tracks batch', status: 'preliminary',
        rung: 0, questions: ['Q1'], supports: ['R1', 'R2'] },
      { id: 'C2', title: 'terminal negative', status: 'refuted',
        rung: 4, questions: ['Q1'], supports: [] },
      { id: 'C3', title: 'off-ladder', status: 'odd',
        rung: null, questions: [], supports: [] },
    ],
    prose: [{ id: 'P1', title: 'What we know about the variance',
              questions: ['Q1'], actor: 'human:u1',
              created_at: '2026-02-01T09:00:00Z' }],
    notes: [{ id: 'N1', title: 'check the calibration log', questions: [],
              actor: 'agent:r9', created_at: '2026-02-02T09:00:00Z' }],
    sediment: {
      runs: [
        { run_id: 'r1', thread_id: 'Q1', state: 'done',
          agent_spec_name: 'guide', turn_index: 3,
          outputs: ['scatter.png', 'profile.svg'],
          started_at: '2026-01-01T10:00:00Z', updated_at: '2026-01-01T10:05:00Z' },
        { run_id: 'r2', thread_id: 'Q1', state: 'failed',
          started_at: '2026-01-01T10:10:00Z', updated_at: '2026-01-01T10:11:00Z' },
        { run_id: 'r3', thread_id: null, state: 'open',
          started_at: '2026-01-02T10:00:00Z', updated_at: null },
      ],
    },
    sittings: [{ id: 'sit-Q1-1', thread_id: 'Q1', run_ids: ['r1', 'r2'],
                 started_at: '2026-01-01T10:00:00Z',
                 ended_at: '2026-01-01T10:11:00Z' }],
    whats_new: [{ id: 2, kind: 'proposal', entity_id: 'Q1',
                  title: 'file the scatter under Q1', ts: '2026-02-03T08:00:00Z' }],
    tray: [{ id: 1, kind: 'route', headline: 'file the scatter under Q1',
             status: 'pending' }],
    leftovers: [{ id: 'A1', type: 'figure-like', title: 'figs/scatter.png' }],
  }
}

describe('apiToWorld', () => {
  it('maps claims onto the renderer maturity ladder', () => {
    const w = apiToWorld(sample())
    expect(w.claims['C1'].maturity).toBe('conjecture')
    expect(w.claims['C1'].evidence).toBe(2)
    expect(w.claims['C2'].maturity).toBe('contested')  // terminal negative
    expect(w.claims['C3'].maturity).toBe('conjecture') // off-ladder floor
  })

  it('shows the pack\'s OWN maturity word, never the fixture ladder\'s', () => {
    const a = sample()
    a.claims[0].maturity = 'preliminary'
    const w = apiToWorld(a)
    // glyph geometry keeps the renderer rung; the READER sees ABA's word
    expect(w.claims['C1'].maturityLabel).toBe('preliminary')
    expect(w.claims['C3'].maturityLabel).toBeUndefined()
  })

  it('inline [[figure:id]] embeds resolve and leave the evidence strip', () => {
    const a = sample()
    a.supports_index = {
      R1: { title: 'batch scatter', type: 'result', artifact: 'scatter.png' },
      R2: { title: 'anova table', type: 'result' },
    }
    a.prose[0].body = 'The scatter shows it.\n[[figure:R1]]\nDone (preliminary).'
    a.prose[0].cites = ['C1']
    const w = apiToWorld(a)
    // the embed resolves: title + artifact + the line to summon the dock on
    expect(w.figureTitles['R1']).toBe('batch scatter')
    expect(w.figureArts?.['R1']).toBe('scatter.png')
    expect(w.figureThreads?.['R1']).toBe('Q1')
    // mentioned once, shown once: R1 left the trailing strip, R2 remains
    const ev = w.sections[0].paragraphs[0].evidence ?? []
    expect(ev.map(e => e.id)).toEqual(['R2'])
  })

  it('carries per-run produced images into expandable sediment rows', () => {
    const w = apiToWorld(sample())
    const r1 = w.sediment.find(e => e.id === 'r1')!
    expect(r1.nOutputs).toBe(2)
    expect(r1.shown.map(o => o.artifact)).toEqual(['scatter.png', 'profile.svg'])
    // a run the API ships without outputs stays honestly un-expandable
    const r2 = w.sediment.find(e => e.id === 'r2')!
    expect(r2.nOutputs).toBe(0)
    expect(r2.shown).toEqual([])
  })

  it('builds sections with open questions, prose stubs, sittings, dormancy', () => {
    const w = apiToWorld(sample())
    expect(w.sections).toHaveLength(2)
    const q1 = w.sections[0]
    expect(q1.question).toBe('what drives variance across siteA runs?')
    // open questions surface as the section PLAN (visible at every phase),
    // not as a stub-only list
    expect(q1.open).toEqual([])
    expect(q1.plan?.map(p => p.text))
      .toEqual(['batch effect?', 'calibration drift?'])
    expect(q1.plan?.every(p => p.state === 'planned')).toBe(true)
    expect(q1.paragraphs[0].text).toBe('What we know about the variance')
    expect(q1.paragraphs[0].ratified.by).toBe('human:u1')
    // when the API ships a prose BODY, the paragraph reads it — the title
    // is only the stand-in for body-less rows
    const withBody = sample()
    withBody.prose[0].body =
      'Variance tracks the batch assignment; drift is ruled out.'
    expect(apiToWorld(withBody).sections[0].paragraphs[0].text)
      .toBe('Variance tracks the batch assignment; drift is ruled out.')
    // episode rows READ: ordinal label, size in meta — never a raw sit id
    expect(q1.sessions?.[0].label).toBe('sitting 1')
    expect(q1.sessions?.[0].meta).toBe('2 runs')
    const q2 = w.sections[1]
    expect(q2.question).toBe('Q2 parked line')   // falls back to title
    expect(q2.dormant?.since).toBe('2026-03-04')
  })

  it('maps runs to sediment: states, named threads, human-only chips', () => {
    const w = apiToWorld(sample())
    expect(w.sediment.map(s => s.state)).toEqual(['ok', 'failed', 'running'])
    // rows carry the NAMED line they worked; unthreaded runs carry none
    expect(w.sediment[0].threadTitle).toBe('Q1 drivers of variance')
    expect(w.sediment[2].threadRef).toBeUndefined()   // background run
    expect(w.sedimentGrain).toBe('thread')
    expect(w.sediment[0].title).toBe('guide · turn 3')
    // an UNdistilled sitting puts no chip on the row (raw ids never render)
    expect(w.sediment[0].sessionRef).toBeUndefined()
    // a distilled one shows its label
    const a = sample()
    a.sittings[0].label = 'traced the reconnect path'
    a.sittings[0].frozen = true
    const w2 = apiToWorld(a)
    expect(w2.sediment[0].sessionLabel).toBe('traced the reconnect path')
  })

  it('carries tray count, events, notes; empty organs stay empty', () => {
    const w = apiToWorld(sample())
    expect(w.pendingDrafts).toBe(1)
    expect(w.whatsNew?.items[0].text).toBe('file the scatter under Q1')
    expect(w.looseNotes[0].origin).toBe('guide')
    expect(w.trails).toEqual([])            // honest: API has no trails yet
    expect(w.provenance).toEqual({})
    expect(w.bare).toBe(false)
  })

  it('a dormant question says what it holds — strongest POSITIVE claim first', () => {
    const a = sample()
    a.questions[1].claims = ['C1', 'C2']      // supported beats refuted
    expect(apiToWorld(a).sections[1].dormant?.holds)
      .toBe('variance tracks batch')
    const only = sample()
    only.questions[1].claims = ['C2']         // a lone negative still shows
    expect(apiToWorld(only).sections[1].dormant?.holds)
      .toBe('terminal negative')
    const b = sample()
    b.questions[1].prose = ['P1']             // no claims → prose stands in
    expect(apiToWorld(b).sections[1].dormant?.holds)
      .toBe('What we know about the variance')
    // with neither, holds stays absent (no invented content)
    expect(apiToWorld(sample()).sections[1].dormant?.holds).toBeUndefined()
  })

  it('sediment windows at scale and reports the total', () => {
    const a = sample()
    a.sediment.runs = Array.from({ length: 130 }, (_, i) => ({
      run_id: `r${i}`, thread_id: 'Q1', state: 'done',
      started_at: `2026-06-01T00:${String(i % 60).padStart(2, '0')}:00Z`,
      updated_at: null,
    }))
    const w = apiToWorld(a)
    expect(w.sediment.length).toBe(60)
    expect(w.sediment[59].id).toBe('r129')     // the RECENT window
    expect(w.sedimentTotal).toBe(130)
    expect(w.bare).toBe(false)                 // windowing must not fake day-0
    // small projects: no windowing, no total banner
    expect(apiToWorld(sample()).sedimentTotal).toBeUndefined()
  })

  it('per-section sitting lists window to the recent six', () => {
    const a = sample()
    a.sittings = Array.from({ length: 10 }, (_, i) => ({
      id: `sit-Q1-${i}`, thread_id: 'Q1', run_ids: [`r${i}`],
      started_at: `2026-06-${String(i + 1).padStart(2, '0')}T09:00:00Z`,
      ended_at: null,
    }))
    const w = apiToWorld(a)
    expect(w.sections[0].sessions?.length).toBe(6)
    // the RECENT window is kept, ordinals stay honest against full history,
    // and the raw sitting id appears in NO row field (reading surface clean)
    expect(w.sections[0].sessions?.[5].label).toBe('sitting 10')
    expect(w.sections[0].sessions?.[5].when).toBe('2026-06-10')
    expect(w.sections[0].sessionsTotal).toBe(10)
    for (const row of w.sections[0].sessions ?? [])
      for (const v of [row.label, row.when, row.meta])
        expect(v).not.toMatch(/sit-|thr_|run_/)
  })

  it('nests subquestions under parents; cycles and unknowns degrade to top level', () => {
    const a = sample()
    a.questions.push(
      { id: 'Q3', title: 'sub-line', question: 'is the effect tunable?',
        open_questions: [], lifecycle: 'open', claims: [], prose: [],
        parent: 'Q1' },
      { id: 'Q4', title: 'orphan', question: null, open_questions: [],
        lifecycle: 'open', claims: [], prose: [], parent: 'QX' },   // unknown
      { id: 'Q5', title: 'loop-a', question: null, open_questions: [],
        lifecycle: 'open', claims: [], prose: [], parent: 'Q6' },
      { id: 'Q6', title: 'loop-b', question: null, open_questions: [],
        lifecycle: 'open', claims: [], prose: [], parent: 'Q5' },
    )
    const w = apiToWorld(a)
    const top = w.sections.map(s => s.id)
    // Q3 nests under Q1; the orphan and BOTH cycle members stay top-level —
    // the face never loses a node
    expect(top).toEqual(['Q1', 'Q2', 'Q4', 'Q5', 'Q6'])
    expect(w.sections[0].children?.map(c => c.id)).toEqual(['Q3'])
    expect(w.sections[0].children?.[0].question).toBe('is the effect tunable?')
  })

  it('a frozen sitting wears its distillation label in the episode row', () => {
    const a = sample()
    a.sittings[0].label = 'traced the reconnect path'
    a.sittings[0].frozen = true
    const w = apiToWorld(a)
    expect(w.sections[0].sessions?.[0].label).toBe('traced the reconnect path')
    expect(w.sections[0].sessions?.[0].meta).toBe('2 runs')
  })

  it('paragraph evidence rides the cited claims; images carry artifacts', () => {
    const a = sample()
    a.prose[0].body = 'Variance tracks batch (supported).'
    a.prose[0].cites = ['C1']
    a.supports_index = {
      R1: { title: 'figs/monthly_offset.png', type: 'figure',
            artifact: 'monthly_offset.png' },
      R2: { title: 'regime summary table', type: 'result' },
    }
    const w = apiToWorld(a)
    const ev = w.sections[0].paragraphs[0].evidence!
    expect(ev.map(e => e.id)).toEqual(['R1', 'R2'])   // C1's supports
    expect(ev[0].artifact).toBe('monthly_offset.png')
    expect(ev[1].artifact).toBeUndefined()
    expect(w.artifactBase).toBe('/artifacts/p-demo/')
  })

  it('revisions carry their version; cited claims retire from the chips', () => {
    const a = sample()
    a.prose[0].body = 'Variance tracks batch (supported).'
    a.prose[0].versions = 2
    a.prose[0].cites = ['C1']
    const w = apiToWorld(a)
    expect(w.sections[0].paragraphs[0].versions).toBe(2)
    // C1 is cited by live prose -> retired; C2 still held
    expect(w.sections[0].claimsHeld).toEqual(['C2'])
    // all claims cited -> the strip retires entirely
    a.prose[0].cites = ['C1', 'C2']
    expect(apiToWorld(a).sections[0].claimsHeld).toBeUndefined()
  })

  it('sections surface held claims as chips before prose cites them', () => {
    const a = sample()
    a.questions[0].prose = []            // no prose yet — chips must carry
    const w = apiToWorld(a)
    expect(w.sections[0].claimsHeld).toEqual(['C1', 'C2'])
    const { container } = render(<Record world={w} />)
    const holds = container.querySelector('.nsec__holds') as HTMLElement
    expect(holds.textContent).toContain('variance tracks batch')
    expect(holds.querySelectorAll('.ref--claim').length).toBe(2)
  })

  it('an empty project renders the bare (day-0) face', () => {
    const a = sample()
    a.questions = []; a.claims = []; a.prose = []; a.notes = []
    a.sediment.runs = []; a.sittings = []; a.whats_new = []; a.tray = []
    const w = apiToWorld(a)
    expect(w.bare).toBe(true)
  })

  it('the Record renderer accepts an adapted world (smoke)', () => {
    const { container } = render(<Record world={apiToWorld(sample())} />)
    const text = container.textContent || ''
    expect(text).toContain('what drives variance across siteA runs?')
    expect(text).toContain('p-demo')
  })

  it('prefers the project display title when the API carries one', () => {
    const a = sample()
    a.project = { title: 'Variance study' }
    expect(apiToWorld(a).project.title).toBe('Variance study')
  })

  it('maps pending proposals into liveTray with section attribution', () => {
    const a = sample()
    a.tray = [
      { id: 7, kind: 'route', headline: 'file it', status: 'pending',
        thread_id: 'Q1' },
      { id: 8, kind: 'claim', headline: 'draft a claim', status: 'pending',
        thread_id: 'nope' },   // unknown thread → no section door
    ]
    const w = apiToWorld(a)
    expect(w.liveTray).toEqual([
      { id: 7, kind: 'route', headline: 'file it', sectionId: 'Q1' },
      { id: 8, kind: 'claim', headline: 'draft a claim' },
    ])
  })

  it('live proposals ride the tray: band count and rows agree', async () => {
    const { fireEvent } = await import('@testing-library/react')
    const a = sample()
    a.tray = [
      { id: 7, kind: 'route', headline: 'file the scatter', status: 'pending',
        thread_id: 'Q1' },
      { id: 8, kind: 'restructure', headline: 'split the section',
        status: 'pending', thread_id: null },
    ]
    const { container } = render(<Record world={apiToWorld(a)} />)
    const band = container.querySelector('.desk__needs') as HTMLElement
    expect(band.textContent).toContain('2')
    fireEvent.click(band)
    const rows = container.querySelectorAll('.tray__row')
    expect(rows.length).toBe(2)
    const text = container.textContent || ''
    expect(text).toContain('route — file the scatter')
    expect(text).toContain('restructure — split the section')
    // routing rows are veto-tier (routine), the restructure is a decision
    expect(container.querySelectorAll('.tray__kind--routine').length).toBe(1)
    expect(container.querySelectorAll('.tray__kind--decision').length).toBe(1)
  })
})

describe('shared triage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('triageApi posts to the classic proposal endpoints', async () => {
    const seen: { url: string; method?: string }[] = []
    vi.stubGlobal('fetch', (async (url: string, init?: RequestInit) => {
      seen.push({ url: String(url), method: init?.method })
      return { ok: true }
    }) as unknown as typeof fetch)
    const t = triageApi('http://x', 'p1')
    await t.accept(7); await t.dismiss(8); await t.undo(7)
    expect(seen).toEqual([
      { url: 'http://x/api/proposals/7/accept?project_id=p1', method: 'POST' },
      { url: 'http://x/api/proposals/8/dismiss?project_id=p1', method: 'POST' },
      { url: 'http://x/api/proposals/7/undo?project_id=p1', method: 'POST' },
    ])
  })

  it('accept removes the row, undo brings it back and calls the API', async () => {
    const { fireEvent, waitFor } = await import('@testing-library/react')
    const calls: string[] = []
    const triage = {
      accept: async (id: number) => { calls.push(`accept:${id}`) },
      dismiss: async (id: number) => { calls.push(`dismiss:${id}`) },
      undo: async (id: number) => { calls.push(`undo:${id}`) },
    }
    const a = sample()
    a.tray = [{ id: 7, kind: 'route', headline: 'file the scatter',
                status: 'pending', thread_id: 'Q1' }]
    const { container } = render(
      <Record world={apiToWorld(a)} triage={triage} />)
    fireEvent.click(container.querySelector('.desk__needs') as HTMLElement)
    expect(container.querySelectorAll('.tray__row').length).toBe(1)
    fireEvent.click(screen.getByText('accept ✓'))
    await waitFor(() =>
      expect(container.querySelectorAll('.tray__row').length).toBe(0))
    expect(calls).toEqual(['accept:7'])
    const undo = container.querySelector('.tray__undo') as HTMLElement
    expect(undo.textContent).toContain('accepted 1')
    fireEvent.click(undo)
    await waitFor(() =>
      expect(container.querySelectorAll('.tray__row').length).toBe(1))
    expect(calls).toEqual(['accept:7', 'undo:7'])
  })

  it('a failed accept keeps the row and says so', async () => {
    const { fireEvent, waitFor } = await import('@testing-library/react')
    const triage = {
      accept: async () => { throw new Error('503') },
      dismiss: async () => {}, undo: async () => {},
    }
    const a = sample()
    a.tray = [{ id: 7, kind: 'route', headline: 'file the scatter',
                status: 'pending', thread_id: 'Q1' }]
    const { container } = render(
      <Record world={apiToWorld(a)} triage={triage} />)
    fireEvent.click(container.querySelector('.desk__needs') as HTMLElement)
    fireEvent.click(screen.getByText('accept ✓'))
    await waitFor(() => expect(
      (container.querySelector('.tray__undo') as HTMLElement).textContent)
      .toContain('failed'))
    expect(container.querySelectorAll('.tray__row').length).toBe(1)
  })

  it('without a triage api, live rows are read-only doors', async () => {
    const { fireEvent } = await import('@testing-library/react')
    const a = sample()
    a.tray = [{ id: 7, kind: 'route', headline: 'file the scatter',
                status: 'pending', thread_id: 'Q1' }]
    const { container } = render(<Record world={apiToWorld(a)} />)
    fireEvent.click(container.querySelector('.desk__needs') as HTMLElement)
    const row = container.querySelector('.tray__row') as HTMLElement
    expect(row.textContent).toContain('go →')
    expect(row.textContent).not.toContain('accept')
    expect(row.textContent).not.toContain('file ✓')
  })
})

describe('the since-cursor', () => {
  // this happy-dom build ships no localStorage; give window a real-enough one
  beforeAll(() => {
    const mem = new Map<string, string>()
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: (k: string) => mem.get(k) ?? null,
        setItem: (k: string, v: string) => { mem.set(k, String(v)) },
        removeItem: (k: string) => { mem.delete(k) },
        clear: () => { mem.clear() },
      },
    })
  })
  afterEach(() => { vi.unstubAllGlobals(); window.localStorage.clear() })

  it('worldUrl threads project and since', () => {
    expect(worldUrl('http://x', 'p1', '2026-01-01T00:00:00Z'))
      .toBe('http://x/api/record/world?project_id=p1&since=2026-01-01T00%3A00%3A00Z')
    expect(worldUrl('http://x')).toBe('http://x/api/record/world')
  })

  it('fetchLiveWorld sends the stored cursor and advances it on success', async () => {
    window.localStorage.setItem('record:lastVisit:p1', '2026-01-05T00:00:00Z')
    const seen: string[] = []
    vi.stubGlobal('fetch', (async (url: string) => {
      seen.push(String(url))
      return { ok: true, json: async () => sample() }
    }) as unknown as typeof fetch)
    const w = await fetchLiveWorld('http://x', 'p1')
    expect(seen[0]).toContain('since=2026-01-05')
    expect(w.whatsNew?.since).toBe('2026-01-05')
    const stored = window.localStorage.getItem('record:lastVisit:p1')!
    expect(stored > '2026-01-05T00:00:00Z').toBe(true)
  })

  it('a failed fetch leaves the cursor untouched', async () => {
    window.localStorage.setItem('record:lastVisit:p1', '2026-01-05T00:00:00Z')
    vi.stubGlobal('fetch', (async () =>
      ({ ok: false, status: 503 })) as unknown as typeof fetch)
    await expect(fetchLiveWorld('http://x', 'p1')).rejects.toThrow('503')
    expect(window.localStorage.getItem('record:lastVisit:p1'))
      .toBe('2026-01-05T00:00:00Z')
  })
})
