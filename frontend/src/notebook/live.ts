/**
 * Live-World adapter — the phase-1 read-only face over a real project
 * (RECORD_DESIGN §13.3). Maps GET /api/record/world (API World v1, built by
 * backend/core/record/world.py) onto the renderer's World.
 *
 * Coverage is HONEST: organs the API does not carry yet render empty — never
 * mimed. Prose paragraphs show the narrative entity's title (bytes stay in
 * the artifact store, phase 3 brings ratified prose); runs surface with the
 * facts the runs table holds. The fixture face remains the default; live
 * mode is opt-in per URL (`?live=1&api=…&project=…`).
 */
import type { World } from './world'
import type { ClaimRef, Section, LooseNote, SedimentEntry } from './fixture'

// ------------------------------------------------------------- API contract

export interface ApiRun {
  run_id: string
  session_id?: string | null
  turn_index?: number | null
  agent_spec_name?: string | null
  state?: string | null
  thread_id?: string | null
  started_at?: string | null
  updated_at?: string | null
}

export interface ApiWorld {
  version: number
  project_id?: string
  project?: { title?: string | null }
  roles: Record<string, string>
  maturity_ladder: string[]
  questions: {
    id: string; title: string | null; question?: string | null
    open_questions?: { text?: string }[] | string[] | null
    lifecycle?: string | null
    claims: string[]; prose: string[]
    created_at?: string; updated_at?: string
  }[]
  claims: {
    id: string; title: string | null; status?: string | null
    rung: number | null; questions: string[]; supports: string[]
    actor?: string | null; created_at?: string; updated_at?: string
  }[]
  prose: { id: string; title: string | null; questions: string[]
           actor?: string | null; created_at?: string }[]
  notes: { id: string; title: string | null; questions: string[]
           actor?: string | null; created_at?: string }[]
  sediment: { runs: ApiRun[] }
  sittings: { id: string; thread_id: string; run_ids: string[]
              started_at?: string | null; ended_at?: string | null }[]
  whats_new: { id: number; kind: string; entity_id?: string | null
               title?: string | null; ts: string }[]
  tray: { id: number; kind: string; headline: string; status: string
          thread_id?: string | null }[]
  leftovers: { id: string; type: string; title: string | null }[]
}

// ------------------------------------------------------------- the mapping

/** Renderer maturity words by rung. The renderer's ladder tops out at
 * 'robust'; a terminal negative ('refuted'-like rungs past 'contested')
 * also renders 'contested' — the epitaph face is a later organ. */
function maturityWord(rung: number | null, ladder: string[]):
    ClaimRef['maturity'] {
  if (rung === null || rung < 0) return 'conjecture'
  const terminal = ladder.length - 1
  if (rung >= 3 || rung === terminal) return 'contested'
  return (['conjecture', 'supported', 'robust'] as const)[Math.min(rung, 2)]
}

function runState(s: string | null | undefined): SedimentEntry['state'] {
  if (s === 'failed') return 'failed'
  if (s === 'done' || s === 'ok' || s === 'complete') return 'ok'
  return 'running'
}

const day = (ts?: string | null) => (ts || '').slice(0, 10)

export function apiToWorld(a: ApiWorld): World {
  const claims: Record<string, ClaimRef> = {}
  for (const c of a.claims) {
    claims[c.id] = {
      title: c.title || c.id,
      maturity: maturityWord(c.rung, a.maturity_ladder),
      evidence: c.supports.length,
      caveats: [],
    }
  }

  const proseById = new Map(a.prose.map(p => [p.id, p]))
  const sitsByThread = new Map<string, ApiWorld['sittings']>()
  for (const s of a.sittings) {
    const arr = sitsByThread.get(s.thread_id) || []
    arr.push(s)
    sitsByThread.set(s.thread_id, arr)
  }

  const sections: Section[] = a.questions.map(q => {
    const open = (q.open_questions || []).map(o =>
      typeof o === 'string' ? o : (o.text || '')).filter(Boolean)
    const sits = sitsByThread.get(q.id) || []
    return {
      id: q.id,
      question: q.question || q.title || q.id,
      phase: 'early',
      paragraphs: q.prose.map(pid => {
        const p = proseById.get(pid)
        return {
          id: pid,
          text: p?.title || pid,
          ratified: { by: p?.actor || '—', on: day(p?.created_at) },
        }
      }),
      addenda: [],
      open,
      sessions: sits.map(s => ({
        label: `${s.run_ids.length} run${s.run_ids.length === 1 ? '' : 's'}`,
        when: day(s.started_at),
        meta: s.id,
      })),
      ...(q.lifecycle === 'parked'
        ? { dormant: { since: day(q.updated_at) } } : {}),
    }
  })

  const looseNotes: LooseNote[] = a.notes.map(n => ({
    id: n.id,
    ts: day(n.created_at),
    origin: (n.actor || '').startsWith('human') ? 'you' : 'guide',
    text: n.title || n.id,
  }))

  const sitOfRun = new Map<string, string>()
  for (const s of a.sittings)
    for (const rid of s.run_ids) sitOfRun.set(rid, s.id)

  const sediment: SedimentEntry[] = a.sediment.runs.map(r => ({
    id: r.run_id,
    date: day(r.started_at || r.updated_at),
    title: [r.agent_spec_name || 'turn',
            r.turn_index != null ? `#${r.turn_index}` : '']
      .filter(Boolean).join(' '),
    state: runState(r.state),
    verdict: '',
    nOutputs: 0,
    shown: [],
    retention: 'kept',
    ...(sitOfRun.has(r.run_id) ? { sessionRef: sitOfRun.get(r.run_id) } : {}),
  }))

  const events = a.whats_new.slice(0, 8)

  return {
    project: {
      title: a.project?.title || a.project_id || 'project',
      started: day(a.sediment.runs[0]?.started_at),
      lastVisit: '',
    },
    whatsNew: events.length
      ? {
          since: '',
          items: events.map(e => ({
            ts: day(e.ts),
            text: e.title || e.kind,
            ...(e.entity_id ? { elId: e.entity_id } : {}),
          })),
        }
      : null,
    pendingDrafts: a.tray.length,
    claims,
    sections,
    trails: [],
    looseNotes,
    sediment,
    provenance: {},
    figureTitles: {},
    bench: {},
    benchFallback: [],
    onePager: null,
    bare: sections.length === 0 && sediment.length === 0,
    sedimentGrain: 'run',
    // the triage band needs a desk; live sittings are all filed episodes, so
    // the line is a plain inventory (open-session tracking is a later organ)
    desk: {
      line: a.sittings.length
        ? `${a.sittings.length} sitting${a.sittings.length === 1 ? '' : 's'} on record`
        : 'no sittings yet',
      items: [],
    },
    liveTray: a.tray.map(p => ({
      id: p.id, kind: p.kind, headline: p.headline,
      ...(p.thread_id && a.questions.some(q => q.id === p.thread_id)
        ? { sectionId: p.thread_id } : {}),
    })),
  }
}

// ------------------------------------------------------------- the fetch

export function worldUrl(api: string, projectId?: string,
                         since?: string | null): string {
  const p = new URLSearchParams()
  if (projectId) p.set('project_id', projectId)
  if (since) p.set('since', since)
  const q = p.toString()
  return `${api}/api/record/world${q ? `?${q}` : ''}`
}

/** The per-user what's-new cursor lives client-side (design §13.3 phase 2:
 *  a last-visit cursor, not a substrate change). Stored per project;
 *  advanced only after a successful fetch, so a failed load loses nothing. */
const sinceKey = (pid?: string) => `record:lastVisit:${pid || 'default'}`

function store(): Storage | undefined {
  try { return typeof window !== 'undefined' ? window.localStorage : undefined }
  catch { return undefined }
}

export async function fetchLiveWorld(api: string, projectId?: string):
    Promise<World> {
  const since = store()?.getItem(sinceKey(projectId)) ?? null
  const res = await fetch(worldUrl(api, projectId, since))
  if (!res.ok) throw new Error(`world fetch failed: ${res.status}`)
  const world = apiToWorld(await res.json() as ApiWorld)
  if (since && world.whatsNew) world.whatsNew.since = since.slice(0, 10)
  store()?.setItem(sinceKey(projectId), new Date().toISOString())
  return world
}
