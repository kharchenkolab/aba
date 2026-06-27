export type Role = 'user' | 'assistant'

export interface TextBlock  { type: 'text';   text: string }
export interface ImageBlock {
  type: 'image';  url: string; alt?: string
  /** Canonical artifact id (<exec_id>:<kind>:<idx>) when this image came
   *  from a tool result that recorded an exec_id (Stage 1+ paths do).
   *  Used by the chat to offer pin-from-artifact (Option B / Phase 3 of
   *  misc/exec_records_and_versioning.md) when the underlying figure
   *  isn't yet materialized as an entity. */
  artifact_id?: string
  /** Rasterized PNG preview for non-raster canonicals (PDF today).
   *  When set, `<img src>` uses this so the browser can actually
   *  display the thumbnail + lightbox; `url` stays the canonical
   *  download. Absent for native rasters (PNG/JPG) — fall back to url. */
  preview_url?: string
}

/** A figure/table reference without a backing entity row. Stage 6 +
 *  Option B introduce this for "unpinned things you can still talk
 *  about and pin if you want." Materialized on pin via
 *  POST /api/artifacts/{exec_id}/{kind}/{idx}/pin. */
export interface ArtifactRef {
  artifact_id: string
  exec_id: string
  kind: 'figure' | 'table' | 'cell' | 'file' | string
  idx: number
  url?: string | null
  original_name?: string
}
/** Transient status line (e.g. "Model is busy — retrying…"); not persisted. */
export interface NoticeBlock { type: 'notice'; text: string }
/** A failed turn — rendered with a retry affordance and expandable detail. */
export interface ErrorBlock { type: 'error'; text: string; detail?: string }
export interface ToolStartBlock {
  type: 'tool_start'
  name: string
  input: Record<string, unknown>
  /** Latest live tool_progress message while the tool runs (installs, compiles,
   *  downloads) — shown next to the spinner so a long call isn't a dead spin. */
  progress?: string
  /** Anthropic tool_use id — used to attach live `tool_chunk` events to the
   *  right tool block (#334 Phase 1). Frontend keys live-stream off this. */
  tool_use_id?: string
  /** Live-tail STATE populated from `tool_chunk` metadata events. Text
   *  itself is fetched on demand (server-side buffer + replay endpoint) —
   *  these fields are the lightweight indicator the button shows without
   *  expanding. liveStdout/liveStderr are populated only while the drawer
   *  is open (ToolStep polls the replay endpoint). */
  liveStdout?: string
  liveStderr?: string
  /** Cumulative bytes per stream (from coalescer's lifetime counter). */
  liveBytesStdout?: number
  liveBytesStderr?: number
  /** Elapsed seconds since the first chunk landed — the in-kernel time, not
   *  wall-clock since tool_start (cells can sit before output begins). */
  liveElapsedS?: number
  /** ms timestamp of the most recent chunk — drives "last activity Xs ago". */
  lastChunkAt?: number
  /** Fix #5 (2026-06-08) — tool returned {deferred:true,job_id} so the turn
   *  halted in AWAITING_TOOL_RESULT. Render a queued badge with the job id
   *  instead of a spinning chip; the eventual tool_result (delivered via the
   *  job-complete webhook) will clear `deferred` and switch to ✓/✗. */
  deferred?: boolean
  deferredJobId?: string
}
export interface ToolResultBlock {
  type: 'tool_result'
  name: string
  result: Record<string, unknown>
  /** Mirrors ToolStartBlock.tool_use_id when present — same purpose. */
  tool_use_id?: string
}
/** A structured step in a presented plan (T2.5). Models may also emit a
 *  plain string, which is coerced to {n, title} server-side. */
export interface PlanStepShape {
  n: number
  title: string
  description?: string
  expected_outputs?: string[]
  skill?: string | null
  parameters?: Record<string, unknown>
}

/** A validator-emitted concern attached to a plan or specific step. */
export interface PlanConcern {
  step_n: number | null   // null = plan-level
  level: 'info' | 'warn' | 'error'
  message: string
}

/** A plan the Guide presented before multi-step work — rendered as a card with
 *  Go / Adjust while it's the latest message and awaiting the user's decision.
 *
 *  Pre-T2.5 the steps were `string[]`; the structured object form is now
 *  authoritative and `string[]` is kept only for backward compat on cached
 *  history blocks. */
export interface PlanBlock {
  type: 'plan'
  title?: string
  summary?: string
  rationale?: string
  assumptions?: string[]
  steps: (PlanStepShape | string)[]
  concerns?: PlanConcern[]
}

export type Block = TextBlock | ImageBlock | ToolStartBlock | ToolResultBlock | NoticeBlock | ErrorBlock | PlanBlock

export interface DisplayMessage {
  id: string
  role: Role
  blocks: Block[]
  ts?: string
}

// SSE events from backend
export interface DeltaEvent       { type: 'delta';       text: string }
export interface ToolStartEvent   { type: 'tool_start';  name: string; input: Record<string, unknown>; tool_use_id?: string }
export interface ToolResultEvent  { type: 'tool_result'; name: string; result: Record<string, unknown>; tool_use_id?: string }
/** #334 — coalesced live stdout/stderr chunk from run_python / run_r, keyed
 *  back to the originating tool_start by `tool_use_id`. */
export interface ToolChunkEvent {
  type: 'tool_chunk'
  tool_use_id: string
  stream: 'stdout' | 'stderr'
  text: string
  bytes_total: number     // cumulative bytes for this stream (lifetime counter)
  elapsed_s: number       // seconds since execute() began
}
export interface DoneEvent        { type: 'done' }
export interface ErrorEvent       { type: 'error';       text: string; detail?: string }
export interface NoticeEvent      { type: 'notice';      text: string }
export interface PlanEvent {
  type: 'plan'
  title?: string
  summary?: string
  rationale?: string
  assumptions?: string[]
  steps: (PlanStepShape | string)[]
  concerns?: PlanConcern[]
}
export interface ManifestEvent    { type: 'manifest'; manifest: ManifestSnapshot; run_id?: string }

/** P-cancel — Guide loop saw a user cancellation. SSE event emitted
 *  just before the turn closes; UI uses it to render a "(cancelled)"
 *  notice instead of treating it as a normal completion. */
export interface CancelledEvent {
  type:   'cancelled'
  reason: string
  run_id: string
}

/** B1 — the Guide paused the turn on ask_clarification. The UI shows the
 *  one-line question with an inline answer input that posts to
 *  /api/turns/{run_id}/resume. */
export interface ClarificationPendingEvent {
  type: 'clarification_pending'
  question: string
  tool_use_id: string
  run_id: string
}

/** Fix #5 — a tool returned {deferred:true, job_id}, halting the turn in
 *  AWAITING_TOOL_RESULT. Frontend clears the tool_start's spinner and shows
 *  a queued badge with the job id. The webhook later posts the real
 *  tool_result, which the UI handles via the normal tool_result branch. */
export interface DeferredToolPendingEvent {
  type: 'deferred_tool_pending'
  tool_name: string
  tool_use_id: string
  deferred_id: string
  run_id: string
}

/** Current pending clarification state exposed by useChat. */
export interface PendingClarification {
  runId: string
  question: string
}

/** P1 #3 — the Guide paused on a per-tool approval. Tool with
 *  approval_policy != 'never' won't run until the user explicitly says
 *  so. Rare by design (the bar is "real money / hard-to-reverse"). */
export interface ApprovalPendingEvent {
  type: 'approval_pending'
  tool_name: string
  summary: string
  tool_use_id: string
  run_id: string
  policy: string
}
export interface PendingApproval {
  runId: string
  toolName: string
  summary: string
  policy: string
}

/** Structured per-turn context (T2.4 Drawer). Mirrors core.manifest.types.Manifest.to_dict(). */
export interface ManifestSnapshot {
  session_id: string
  turn_index: number
  focus: {
    entity_id: string
    entity_type: string
    title: string
    status: string
    text: string
    fields_loaded: string[]
  } | null
  thread: {
    thread_id: string
    text: string
  } | null
  policy_text: string
}

export interface EntityRegisteredEvent {
  type: 'entity_registered'
  entity: Entity
}

/** #1 — live phase/progress for a long synchronous tool call (installs,
 *  kernel exec, nextflow). Streamed between tool_start and tool_result. */
export interface ToolProgressEvent {
  type: 'tool_progress'
  name: string
  message: string
  phase?: string
  tool_use_id?: string
}

/** A background job was submitted (run_python background=true). */
export interface JobSubmittedEvent {
  type: 'job_submitted'
  job: { id: string; status?: string; title?: string }
}

/** Observability Console: one captured SSE event (delta excluded — that's
 *  the chat text). `level` gates it in the detail-level selector. */
export interface LogEntry {
  t: number              // epoch ms
  type: string
  label: string
  level: 1 | 2 | 3       // 1=progress, 2=tools, 3=debug
}

/** Observability Jobs tab — a background job's last-known state. */
export interface JobInfo {
  id: string
  status: string
  title?: string
  t: number
}

export type SSEEvent =
  | DeltaEvent
  | ToolStartEvent
  | ToolResultEvent
  | ToolProgressEvent
  | ToolChunkEvent
  | JobSubmittedEvent
  | EntityRegisteredEvent
  | DoneEvent
  | ErrorEvent
  | NoticeEvent
  | PlanEvent
  | ManifestEvent
  | ClarificationPendingEvent
  | ApprovalPendingEvent
  | CancelledEvent
  | DeferredToolPendingEvent

// ---------- Entities ----------

export type EntityType =
  | 'workspace'
  | 'dataset'
  | 'analysis'
  | 'figure'
  | 'table'
  | 'result'
  | 'finding'
  | 'claim'
  | 'narrative'
  | 'thread'
  | 'note'
  | 'plan'

/** A panel within a kept Result (observation). Figures/tables/values reference
 *  a cell entity by id; text panels carry inline prose. */
export interface ResultMember {
  id: string
  kind: 'figure' | 'table' | 'value' | 'text'
  ref?: string
  text?: string
  caption?: string
  /** 'ai' when caption was generated by the auto-interpret daemon; flips
   *  to 'user' on first edit so the ✨ AI tag disappears and the daemon
   *  won't overwrite. */
  caption_origin?: 'ai' | 'user'
}

export interface Entity {
  id: string
  type: EntityType
  title: string
  status: string
  artifact_path: string | null
  producing_params: Record<string, unknown> | null
  parent_entity_id: string | null
  scenario_of: string | null
  metadata: Record<string, unknown> | null
  tags: string[]
  notes: string | null
  pinned: boolean
  // Post Cutover 4 (misc/exec_records_and_versioning.md): pointer to
  // the exec record that produced this entity. The legacy
  // `producing_code` column is gone — code is reachable through the
  // exec record (GET /api/entities/{id}/revisions for the chain,
  // make_revision / reproduce for operations).
  exec_id: string | null
  artifact_kind: string | null
  artifact_idx: number | null
  // Phase 2 (provenance): how this entity came to be + who made it.
  derivation: { kind: string; sources?: string[]; exec_id?: string; source?: string } | null
  actor: string | null
  deleted_at: string | null
  created_at: string
  updated_at: string
}
