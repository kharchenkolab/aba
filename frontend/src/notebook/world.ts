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
  /** deixis, chat → doc: the message can point at a document element */
  ref?: { el: string; label: string }
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
  /** the impact set — record elements this session has landed things on */
  touched?: string[]
  /** deixis, doc → chat: the conversation's current subject; clicking a
   *  document element updates it live — pointing replaces context-setting */
  lookingAt?: string
  /** cross-boundary relevance stays a PROPOSAL — the agent never writes
   *  outside the anchor silently */
  crossFlag?: { text: string; accept: string }
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
  /** every item is a DOOR when elId is set — the strip names work AND takes you there */
  items: { ts: string; text: string; loud?: boolean; live?: boolean; elId?: string }[]
}

export interface OnePager {
  dataLine: string; methodLine: string; number: string; caveat: string
}

// ------------------------------------------------------------------- spine
// The Record RECURSES. A flat page holds one question's active working set
// (~5–8 live narrative lines) — roughly a tenth of one paper's project once
// the unpublished 5–10× (negative results, alternative attempts) is counted.
// So a mature project is a SPINE plus question pages: the spine is the
// project-grain face — a rolling ratified abstract over ARCS (the aims /
// result-lines of the paper), each question ONE line whose face follows its
// state; the full detail face (today's whole prototype) lives one level
// down, per question. Depth follows the science; compaction is the common
// case, not the edge case — the spine is mostly one-liners with a few open
// sections, like a table of contents with three chapters open.

/** One question on the spine — one line, face by state.
 *  open   — active working set: a "now" line, live badges, descend door
 *  held   — dormant with the claim it holds + wake door
 *  closed — reads like a published abstract line: the ratified verdict
 *  dead   — an EPITAPH: hypothesis · verdict · the run that killed it ·
 *           date. One line forever, searchable — the paper reports the
 *           survivors; the record keeps the casualties. */
export interface SpineQ {
  id: string
  title: string
  state: 'open' | 'held' | 'closed' | 'dead'
  /** the claim this question currently holds (closed/held), maturity in text */
  holds?: string
  /** open questions: where the work stands right now */
  now?: string
  /** held: dormant since */
  since?: string
  /** dead: how the line died */
  epitaph?: { verdict: string; run: string; date: string }
  /** a session standing on this question (▷ at rest / ▶ live) */
  session?: { label: string; live?: boolean }
  /** open rows: recency ("today · 3 runs") */
  activity?: string
  /** pending items ON this question's subtree, surfaced for the tray —
   *  same parity discipline: the band count, the tray rows, and these
   *  badges are one derivation */
  pending?: { key: string; kind: 'addendum' | 'fragment' | 'note' | 'claim draft'; label: string; routine: boolean }[]
}

export interface SpineArc {
  id: string
  title: string
  era: string                    // "y1", "y2–y3", "cross-cutting"
  questions: SpineQ[]
  runs?: number                  // this arc's share of the sediment
  /** arcs default COLLAPSED (header only) unless open — the inverted face */
  open?: boolean
  /** the folded arc's ABSTRACT face: what the whole chapter holds, one line */
  holds?: string
}

export interface Spine {
  /** the rolling synthesis — ratified like any prose; consolidation is a
   *  ratification event: a new synthesis SUPERSEDES (never rewrites) the
   *  last, which archives beneath it, still cited */
  abstract: { text: string }[]
  synthesisNote: string          // "rolling synthesis · re-ratified …"
  superseded?: { label: string; note: string }
  arcs: SpineArc[]
  sessionsTotal: number
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
  /** live mode: pending proposals from the shared store, rendered into the
   *  SAME tray as the derived pending items (one derivation discipline —
   *  §13.3 phase 2: ratifying here and in the classic UI is the same row) */
  liveTray?: { id: number; kind: string; headline: string; sectionId?: string }[]
  /** the desk strip — open/last sessions, the resume point */
  desk?: DeskState
  /** a working panel open over the document */
  panel?: PanelState
  /** the sessions on the record — episodes behind the redux (▷/▶ targets) */
  sessions?: SessionRec[]
  /** open this session's page on first render (storyboard scenes) */
  openSession?: { id: string; turn?: number }
  /** initial grain of the work-record stratum */
  sedimentGrain?: 'run' | 'session' | 'thread'
  /** when set, each section links to its chat thread at `<base><sectionId>`
   *  (the classic workspace's canonical thread URL) */
  threadHrefBase?: string
  /** base URL for inline evidence images: `<artifactBase><artifactName>` */
  artifactBase?: string
  /** the live session's home locus — wears a standing "working here" state */
  anchorAt?: { session: string; elId: string }
  /** peripheral change signals: TOC pulse badges + delta-rail ticks.
   *  Three tiers only: accretion (routine, teal — clears on view) · draft
   *  (awaiting you, amber — until acted; ALSO derived from pending state)
   *  · condition (loud, red — until resolved). Anchoring rule: the
   *  viewport holds a visible landmark steady (scroll-anchoring on an
   *  element near the top/middle of view); updates landing IN view
   *  materialize where they land — best seen, not suppressed — and
   *  out-of-view updates go to the periphery. The page never jumps. */
  deltas?: { elId: string; kind: 'accretion' | 'draft' | 'condition'; count?: number; label: string }[]
  /** total runs when the sediment shows only its recent window (scale face) */
  sedimentTotal?: number
  /** the figure the weekly digest leads with */
  digestFig?: string
  /** sediment entries expanded on first render */
  openSediment?: string[]
  /** render "▷ work" affordances (the play button pointed at anchors —
   *  sittings open on the anchor's thread; work never creates a thread) */
  work?: boolean
  /** the project-grain face: spine over arcs (replaces the strata) */
  spine?: Spine
  /** a question page one level below the spine — breadcrumb back up */
  crumb?: { up: string; arc: string }

  // ---------------------------------------------- editorial governance (§14)

  /** re-entry past a few days is a BRIEFING, not a diff: authored prose,
   *  past tense, ranked by consequence, scaled to time away — and it flags
   *  what it could not resolve. Content stayed current; structure held. */
  briefing?: {
    away: string
    paras: { text: string; elId?: string }[]
    flag?: { text: string; elId?: string }
    held: string
  }
  /** the trust ratchet, downward: after a run of accepts the system
   *  proposes lowering its own ceremony — visibly, and reversibly */
  ratchet?: { text: string }
  /** a batched RESTRUCTURING PROPOSAL — structural change arrives as ONE
   *  artifact, reviewed in one sitting. Hysteresis, not weather: proposed
   *  only after the preference held across cycles; each item priced in
   *  reader-visible units, with the rejected alternative shown; "never"
   *  writes a rule. Class 2 applies by default (visible expiry, veto);
   *  class 3 waits indefinitely. */
  rfc?: {
    title: string
    note: string
    items: {
      verb: string          // fold · split · promote · demote
      what: string
      why: string           // in evidence terms
      impact: string        // what it costs the READER; renditions do the work
      alt?: string          // the alternative considered, and why rejected
      cls: 2 | 3
      expires?: string
    }[]
  }
}

export const coastalWorld: World = {
  project, whatsNew, pendingDrafts, claims, sections, trails, looseNotes,
  sediment, provenance, figureTitles, bench, benchFallback, onePager,
  openSediment: ['run_qc'],
}
