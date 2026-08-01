/** Live-World adapter guards: API World v1 → renderer World mapping, and a
 *  smoke render of the real Record component over an adapted world. Honest
 *  coverage: what the API lacks must come out EMPTY, not mimed. */
import { describe, it, expect, vi, afterEach, beforeAll } from 'vitest'
import { render, screen } from '@testing-library/react'
import Record from './Record'
import { apiToWorld, fetchLiveWorld, worldUrl, type ApiWorld } from './live'

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

  it('builds sections with open questions, prose stubs, sittings, dormancy', () => {
    const w = apiToWorld(sample())
    expect(w.sections).toHaveLength(2)
    const q1 = w.sections[0]
    expect(q1.question).toBe('what drives variance across siteA runs?')
    expect(q1.open).toEqual(['batch effect?', 'calibration drift?'])
    expect(q1.paragraphs[0].text).toBe('What we know about the variance')
    expect(q1.paragraphs[0].ratified.by).toBe('human:u1')
    expect(q1.sessions?.[0].label).toBe('2 runs')
    const q2 = w.sections[1]
    expect(q2.question).toBe('Q2 parked line')   // falls back to title
    expect(q2.dormant?.since).toBe('2026-03-04')
  })

  it('maps runs to sediment with states and sitting refs', () => {
    const w = apiToWorld(sample())
    expect(w.sediment.map(s => s.state)).toEqual(['ok', 'failed', 'running'])
    expect(w.sediment[0].sessionRef).toBe('sit-Q1-1')
    expect(w.sediment[2].sessionRef).toBeUndefined()  // background run
    expect(w.sediment[0].title).toBe('guide #3')
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
