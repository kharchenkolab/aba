/**
 * Live-World adapter — the phase-1 read-only face over a real project
 * (RECORD_DESIGN §13.3). Maps GET /api/record/world (API World v1, built by
 * backend/core/record/world.py) onto the renderer's World.
 *
 * Coverage is HONEST: organs the API does not carry yet render empty — never
 * mimed. Prose paragraphs render the entity's body when the API ships one
 * (`prose_body_key` registration), its title otherwise; runs surface with
 * the facts the runs table holds. The fixture face remains the default;
 * live mode is opt-in per URL (`?live=1&api=…&project=…`).
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
    maturity?: string | null
    rung: number | null; questions: string[]; supports: string[]
    caveats?: string[]; evidence?: number
    actor?: string | null; created_at?: string; updated_at?: string
  }[]
  prose: { id: string; title: string | null; questions: string[]
           body?: string | null
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
      evidence: c.evidence ?? c.supports.length,
      caveats: c.caveats || [],
    }
  }

  const proseById = new Map(a.prose.map(p => [p.id, p]))
  const sitsByThread = new Map<string, ApiWorld['sittings']>()
  for (const s of a.sittings) {
    const arr = sitsByThread.get(s.thread_id) || []
    arr.push(s)
    sitsByThread.set(s.thread_id, arr)
  }

  const claimById = new Map(a.claims.map(c => [c.id, c]))
  const sections: Section[] = a.questions.map(q => {
    const open = (q.open_questions || []).map(o =>
      typeof o === 'string' ? o : (o.text || '')).filter(Boolean)
    const sits = sitsByThread.get(q.id) || []
    // a dormant line must say what it HOLDS — the strongest POSITIVE claim
    // under it (contested/refuted are terminal negatives, not high rungs),
    // else its prose title, else any claim — never silently nothing
    const qClaims = q.claims.map(id => claimById.get(id)!).filter(Boolean)
    const pos = (r: number | null) => (r === null || r >= 3 ? -1 : r)
    const best = [...qClaims].sort((x, y) => pos(y.rung) - pos(x.rung))[0]
    const holds = (best && pos(best.rung) >= 0 ? best.title : undefined)
      || proseById.get(q.prose[0] || '')?.title
      || qClaims[0]?.title
    return {
      id: q.id,
      question: q.question || q.title || q.id,
      phase: 'early',
      paragraphs: q.prose.map(pid => {
        const p = proseById.get(pid)
        return {
          id: pid,
          // the story stratum reads: the prose BODY when the API carries it
          // (phase 3), the title as an honest stand-in otherwise
          text: p?.body || p?.title || pid,
          ratified: { by: p?.actor || '—', on: day(p?.created_at) },
        }
      }),
      addenda: [],
      // pre-prose: surface held claims as chips (they retire into prose
      // citations at phase 3); cap for legibility at scale
      ...(q.claims.length ? { claimsHeld: q.claims.slice(0, 8) } : {}),
      open,
      // recent sittings only — an old question's episode list must not grow
      // without bound (find the rest through the sediment). Labels are for
      // READING: ordinal + date + size; the sitting id stays in the sediment
      // stratum (sessionRef), never inline in the story.
      sessions: sits.slice(-6).map((s, i) => ({
        label: `sitting ${sits.length - Math.min(sits.length, 6) + i + 1}`,
        when: day(s.started_at),
        meta: `${s.run_ids.length} run${s.run_ids.length === 1 ? '' : 's'}`,
      })),
      ...(sits.length > 6 ? { sessionsTotal: sits.length } : {}),
      ...(q.lifecycle === 'parked'
        ? { dormant: { since: day(q.updated_at),
                       ...(holds ? { holds } : {}) } } : {}),
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

  // scale face: the sediment shows a recent window; the total rides the
  // header ("N runs · showing recent") — legibility does not decay with age
  const SEDIMENT_WINDOW = 60
  const allRuns = a.sediment.runs
  const windowed = allRuns.slice(-SEDIMENT_WINDOW)

  const sediment: SedimentEntry[] = windowed.map(r => ({
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
    bare: sections.length === 0 && allRuns.length === 0,
    sedimentGrain: 'run',
    ...(allRuns.length > windowed.length
      ? { sedimentTotal: allRuns.length } : {}),
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

// ------------------------------------------------------------- triage

/** Phase-2 shared triage: the face calls the SAME proposal endpoints the
 *  classic UI uses (accept fires the proposal's handler server-side; undo
 *  reverses a recent decision). Only meaningful against a full backend —
 *  the read-only sidecar does not mount these routes. */
export interface TriageApi {
  accept(id: number): Promise<void>
  dismiss(id: number): Promise<void>
  undo(id: number): Promise<void>
}

export function triageApi(api: string, projectId?: string): TriageApi {
  const call = async (id: number, verb: string) => {
    const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
    const res = await fetch(`${api}/api/proposals/${id}/${verb}${q}`,
                            { method: 'POST' })
    if (!res.ok) throw new Error(`${verb} failed: ${res.status}`)
  }
  return {
    accept: id => call(id, 'accept'),
    dismiss: id => call(id, 'dismiss'),
    undo: id => call(id, 'undo'),
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
