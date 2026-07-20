/**
 * World — everything one Record screen renders, as a single value.
 *
 * The Record was first written against module-level fixture imports (one
 * world, one screen). The workflow storyboard needs the same renderer over
 * many worlds — snapshots of a project at different moments of its life —
 * so the fixture is bundled into a World object and threaded through the
 * component tree. `/notebook.html` renders `coastalWorld` exactly as before.
 *
 * The work-loop additions (desk strip, working panel, archived transcripts)
 * are part of the World too: a scene IS a world, including what the
 * scientist has open at that moment.
 */
import {
  project, whatsNew, pendingDrafts, claims, sections, trails, looseNotes,
  sediment, provenance, figureTitles, bench, benchFallback, onePager,
  type ClaimRef, type Section, type Trail, type LooseNote,
  type SedimentEntry, type Prov, type BenchMsg,
} from './fixture'

// ---------------------------------------------------------------- work loop

/** One message in a working panel. Exactly one of the payload fields set. */
export interface PanelMsg {
  role: 'you' | 'guide' | 'system'
  text?: string
  /** a run launched from this exchange — the card mirrors its sediment line */
  run?: { title: string; state: 'running' | 'ok'; meta: string }
  /** a result returned into the conversation */
  fig?: { id: string; stat: string }
  /** quiet system line — e.g. "fragment drafted → T1 ↓" */
  note?: string
}

/** Session-close face: the distillation moment. */
export interface PanelClose {
  summary: string
  distillates: { text: string; dest: string; state: 'accepted' | 'to inbox' }[]
}

export interface PanelState {
  /** where the panel was opened from — chips the agent already has in scope */
  scope: { kind: 'project' | 'question' | 'trail' | 'figure' | 'result'; label: string }[]
  scopeNote?: string
  status?: string                  // header line, e.g. "session open · 48 min"
  msgs: PanelMsg[]
  closing?: PanelClose
  archived?: { label: string; when: string }   // read-only transcript view
}

export interface DeskState {
  /** e.g. "no open sessions" / "1 open session" */
  line: string
  items: { label: string; meta: string; live?: boolean; action?: string; sessionId?: string }[]
}

/**
 * A session on the record — the full episode, not just its distillate.
 * The redux is a map; sessions are the territory: they hold the whole
 * exchange (addressable by turn), every artifact touched — including the
 * LEFTOVERS nobody pinned, noted, or discussed — and they stay
 * continuable. Findable by time (work record, by-session grain), by
 * anchor (section lists, desk), and by what was SAID (transcript search).
 */
export interface SessionRec {
  id: string                    // stable handle; sediment sessionRef points here
  label: string                 // auto-titled, human-renamable ("winter dig")
  when: string
  state: 'open' | 'parked' | 'filed'
  anchor: { kind: 'project' | 'question' | 'trail' | 'figure' | 'result'; label: string }
  scope: PanelState['scope']
  msgs: PanelMsg[]              // the transcript; you/guide msgs are numbered turns
  turns: number
  /** what entered the record from here (the distillate, post-ratification) */
  distillate: { text: string; dest: string }[]
  /** produced but never pinned / noted / discussed — surfaced for late review */
  leftovers: { id: string; title: string; note?: string }[]
  /** chain edges: this sitting picks up an earlier line of work */
  continues?: string
  continuedBy?: string
}

// -------------------------------------------------------------------- world

export interface WhatsNew {
  since: string
  items: { ts: string; text: string; loud?: boolean; live?: boolean }[]
}

export interface OnePager {
  dataLine: string; methodLine: string; number: string; caveat: string
}

export interface World {
  project: { title: string; started: string; lastVisit: string }
  whatsNew: WhatsNew | null
  pendingDrafts: number
  claims: Record<string, ClaimRef>
  sections: Section[]
  trails: Trail[]
  looseNotes: LooseNote[]
  sediment: SedimentEntry[]
  provenance: Record<string, Prov>
  figureTitles: Record<string, string>
  bench: Record<string, BenchMsg[]>
  benchFallback: BenchMsg[]
  onePager: OnePager | null

  /** day-0 face: the project is a composer, not a document yet */
  bare?: boolean
  /** the desk strip — open/last sessions, the resume point */
  desk?: DeskState
  /** a working panel open over the document */
  panel?: PanelState
  /** the sessions on the record — episodes behind the redux (⟲ targets) */
  sessions?: SessionRec[]
  /** open this session's page on first render (storyboard scenes) */
  openSession?: { id: string; turn?: number }
  /** initial grain of the work-record stratum */
  sedimentGrain?: 'run' | 'session'
  /** sediment entries expanded on first render */
  openSediment?: string[]
  /** render "work ▸" affordances on section heads (the work loop is wired) */
  work?: boolean
}

export const coastalWorld: World = {
  project, whatsNew, pendingDrafts, claims, sections, trails, looseNotes,
  sediment, provenance, figureTitles, bench, benchFallback, onePager,
  openSediment: ['run_qc'],
}
