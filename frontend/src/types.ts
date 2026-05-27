export type Role = 'user' | 'assistant'

export interface TextBlock  { type: 'text';   text: string }
export interface ImageBlock { type: 'image';  url: string; alt?: string }
/** Transient status line (e.g. "Model is busy — retrying…"); not persisted. */
export interface NoticeBlock { type: 'notice'; text: string }
/** A failed turn — rendered with a retry affordance and expandable detail. */
export interface ErrorBlock { type: 'error'; text: string; detail?: string }
export interface ToolStartBlock {
  type: 'tool_start'
  name: string
  input: Record<string, unknown>
}
export interface ToolResultBlock {
  type: 'tool_result'
  name: string
  result: Record<string, unknown>
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
export interface ToolStartEvent   { type: 'tool_start';  name: string; input: Record<string, unknown> }
export interface ToolResultEvent  { type: 'tool_result'; name: string; result: Record<string, unknown> }
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
export interface ManifestEvent    { type: 'manifest'; manifest: ManifestSnapshot }

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

export type SSEEvent =
  | DeltaEvent
  | ToolStartEvent
  | ToolResultEvent
  | EntityRegisteredEvent
  | DoneEvent
  | ErrorEvent
  | NoticeEvent
  | PlanEvent
  | ManifestEvent

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

/** A panel within a kept Result (observation). Figures/tables/values reference
 *  a cell entity by id; text panels carry inline prose. */
export interface ResultMember {
  id: string
  kind: 'figure' | 'table' | 'value' | 'text'
  ref?: string
  text?: string
  caption?: string
}

export interface Entity {
  id: string
  type: EntityType
  title: string
  status: string
  artifact_path: string | null
  producing_code: string | null
  producing_params: Record<string, unknown> | null
  parent_entity_id: string | null
  scenario_of: string | null
  metadata: Record<string, unknown> | null
  tags: string[]
  notes: string | null
  pinned: boolean
  deleted_at: string | null
  created_at: string
  updated_at: string
}
