/** Work-dock guards: the anchor pane renders its kind, the transcript
 *  renders REAL markdown, the composer posts to the SAME /api/chat the
 *  workspace uses (thread + focus riding along), and a plan anchor seeds
 *  the composer without auto-firing. */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import WorkDock, { mdBlocks } from './dock'
import type { World } from './world'

const MSGS = [
  { role: 'user', content: [{ type: 'text', text: 'map the variance drivers' }], ts: '2026-01-01T09:00:00Z' },
  { role: 'assistant', content: [{ type: 'tool_use' }], ts: '2026-01-01T09:00:20Z' },
  { role: 'assistant', content: [{ type: 'text', text: '## Snapshot\n\n- **4,320 rows**, no gaps\n- `reading` matches' }], ts: '2026-01-01T09:01:00Z' },
]

function liveWorld(over: Partial<World> = {}): World {
  return {
    project: { title: 'T', started: '', lastVisit: '' },
    whatsNew: null, pendingDrafts: 0,
    claims: {
      C1: { title: 'variance tracks batch', maturity: 'conjecture',
            maturityLabel: 'preliminary', evidence: 2, caveats: ['n small'],
            statement: 'variance tracks batch, full statement.',
            supportRefs: [
              { id: 'R1', title: 'batch scatter', type: 'result', artifact: 'scatter.png' },
              { id: 'R2', title: 'anova table', type: 'result' },
            ] },
    },
    sections: [], trails: [], looseNotes: [],
    sediment: [
      { id: 'r1', date: '2026-01-01', ts: '2026-01-01T09:00:30Z',
        title: 'map the variance drivers', state: 'ok', verdict: '',
        nOutputs: 1, retention: 'kept', threadRef: 'Q1',
        shown: [{ id: 'r1-out-0', kind: 'figure', title: 'batch scatter', artifact: 'scatter.png' }] },
    ],
    provenance: {}, figureTitles: {}, bench: {}, benchFallback: [], onePager: null,
    apiBase: '', projectId: 'p-demo',
    artifactBase: '/artifacts/p-demo/', threadHrefBase: '/p/p-demo/threads/t/',
    ...over,
  }
}

function mockFetch(routes: Record<string, unknown>) {
  return vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url)
    for (const [frag, body] of Object.entries(routes)) {
      if (u.includes(frag)) {
        return { ok: true, json: async () => body,
                 body: { cancel: () => {} } } as unknown as Response
      }
    }
    return { ok: false, status: 404, json: async () => ({}),
             body: { cancel: () => {} } } as unknown as Response
  })
}

afterEach(() => vi.restoreAllMocks())

describe('mdBlocks', () => {
  it('renders headings, bold, code, lists as elements — never raw marks', () => {
    const { container } = render(<div>{mdBlocks('## Snapshot\n\n- **4,320 rows**\n- `reading` ok\n\nplain text')}</div>)
    expect(container.querySelector('.dk__h')?.textContent).toBe('Snapshot')
    expect(container.querySelector('li b')?.textContent).toBe('4,320 rows')
    expect(container.querySelector('li code')?.textContent).toBe('reading')
    expect(container.textContent).not.toContain('##')
    expect(container.textContent).not.toContain('**')
  })
})

describe('WorkDock', () => {
  it('renders the real transcript with working steps and stitched outputs', async () => {
    vi.stubGlobal('fetch', mockFetch({
      '/messages?': MSGS, '/active-turn': null,
    }))
    const { container } = render(
      <WorkDock w={liveWorld()} anchor={{ kind: 'question', threadId: 'Q1', title: 'Q1?' }}
                onClose={() => {}} />)
    await waitFor(() => screen.getByText('map the variance drivers'))
    screen.getByText(/1 working step/)
    // the step gap opens onto what the step LEFT — the run's figure
    expect(container.querySelector('.dk__outs img')?.getAttribute('src'))
      .toBe('/artifacts/p-demo/scatter.png')
    // markdown rendered, not raw
    expect(container.textContent).not.toContain('##')
  })

  it('claim anchor: the dossier lists evidence BY NAME, never a bare count', async () => {
    vi.stubGlobal('fetch', mockFetch({ '/messages?': [], '/active-turn': null }))
    render(
      <WorkDock w={liveWorld()}
                anchor={{ kind: 'claim', threadId: 'Q1', entityId: 'C1', title: 'variance tracks batch' }}
                onClose={() => {}} />)
    screen.getByText('variance tracks batch, full statement.')
    screen.getByText(/preliminary · 2 pieces of evidence/)
    screen.getByText('batch scatter')
    screen.getByText('anova table')
    screen.getByText(/caveats: n small/)
  })

  it('the composer POSTS to /api/chat with thread, project and focus', async () => {
    const f = mockFetch({ '/messages?': [], '/active-turn': null, '/api/chat': {} })
    vi.stubGlobal('fetch', f)
    render(
      <WorkDock w={liveWorld()}
                anchor={{ kind: 'claim', threadId: 'Q1', entityId: 'C1', title: 't' }}
                onClose={() => {}} />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'contest this' } })
    fireEvent.click(screen.getByText('send'))
    await waitFor(() => {
      const call = f.mock.calls.find(c => String(c[0]).includes('/api/chat'))
      expect(call).toBeTruthy()
      const body = JSON.parse(String((call![1] as RequestInit).body))
      expect(body).toMatchObject({
        text: 'contest this', thread_id: 'Q1',
        project_id: 'p-demo', focus_entity_id: 'C1',
      })
    })
    screen.getByText(/the guide is working/)
  })

  it('plan anchor: the item SEEDS the composer — reviewed, never auto-sent', async () => {
    const f = mockFetch({ '/messages?': [], '/active-turn': null, '/api/chat': {} })
    vi.stubGlobal('fetch', f)
    render(
      <WorkDock w={liveWorld()}
                anchor={{ kind: 'plan', threadId: 'Q1', title: 'Q1?',
                          seed: 'compare enclosure shading between the arrays' }}
                onClose={() => {}} />)
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement
    expect(ta.value).toBe('compare enclosure shading between the arrays')
    // nothing fired yet
    expect(f.mock.calls.some(c => String(c[0]).includes('/api/chat'))).toBe(false)
    screen.getByText('▷ launch')
  })

  it('a turn already live on the line surfaces as working on open', async () => {
    vi.stubGlobal('fetch', mockFetch({
      '/messages?': [], '/active-turn': { run_id: 'rr1', state: 'running' },
    }))
    render(
      <WorkDock w={liveWorld()} anchor={{ threadId: 'Q1', title: 'Q1?' }}
                onClose={() => {}} />)
    await waitFor(() => screen.getByText(/the guide is working/))
  })

  it('awaiting_user routes the next message through resume, labeled answer', async () => {
    const f = mockFetch({
      '/messages?': [], '/active-turn': { run_id: 'rr1', state: 'awaiting_user' },
      '/api/turns/rr1/resume': {},
    })
    vi.stubGlobal('fetch', f)
    render(
      <WorkDock w={liveWorld()} anchor={{ threadId: 'Q1', title: 'Q1?' }}
                onClose={() => {}} />)
    await waitFor(() => screen.getByText(/asking YOU/))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'yes, proceed' } })
    fireEvent.click(screen.getByText('answer'))
    await waitFor(() => {
      const call = f.mock.calls.find(c => String(c[0]).includes('/resume'))
      expect(call).toBeTruthy()
      const body = JSON.parse(String((call![1] as RequestInit).body))
      expect(body.user_text).toBe('yes, proceed')
    })
  })
})
