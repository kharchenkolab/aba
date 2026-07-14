// GENERATED FILE — do not edit.
// Source of truth: backend/core/runtime/wire.py
// Regenerate:      python scripts/gen_wire_types.py
// Sync-guarded by  tests/test_wire_contract.py

import type { Entity, JobRow, ManifestSnapshot, ModuleEnableOffer, PlanConcern, PlanStepShape } from './types';

/** Wire framing adds a monotonic seq to every turn-channel event. */
export interface TurnEventBase {
  seq?: number;
}

/** Turn-start drawer sidecar: the structured Manifest snapshot + the run_id the client needs for Stop/reattach. */
export interface ManifestEvent extends TurnEventBase {
  type: 'manifest';
  manifest: ManifestSnapshot;
  run_id: string;
}

/** A streamed chunk of assistant text. */
export interface DeltaEvent extends TurnEventBase {
  type: 'delta';
  text: string;
}

/** The model issued a tool_use block; UI renders the running chip. */
export interface ToolStartEvent extends TurnEventBase {
  type: 'tool_start';
  name: string;
  input: Record<string, unknown>;
  tool_use_id: string;
}

/** A coarse progress line for a running tool (phase ticks). */
export interface ToolProgressEvent extends TurnEventBase {
  type: 'tool_progress';
  name: string;
  tool_use_id: string;
  message?: string | null;
  phase?: string | null;
}

/** Live output tail from a running tool (stdout/stderr), coalesced. */
export interface ToolChunkEvent extends TurnEventBase {
  type: 'tool_chunk';
  tool_use_id: string;
  stream: 'stdout' | 'stderr';
  text: string;
  bytes_total: number;
  elapsed_s: number;
}

/** A finished tool call's result envelope. */
export interface ToolResultEvent extends TurnEventBase {
  type: 'tool_result';
  name: string;
  result: Record<string, unknown>;
  tool_use_id: string;
}

/** A new entity was minted during the turn (artifact registrar / create_scenario). */
export interface EntityRegisteredEvent extends TurnEventBase {
  type: 'entity_registered';
  entity: Entity;
}

/** present_plan halt-after card: the structured plan (steps enriched with param_form where a pipeline schema is known). */
export interface PlanEvent extends TurnEventBase {
  type: 'plan';
  entity_id: string;
  title: string;
  summary: string;
  rationale: string;
  assumptions: string[];
  steps: (PlanStepShape | string)[];
  concerns: PlanConcern[];
}

/** ask_clarification halt-after: the question, plus one-click Enable options when it is about a turned-off module. */
export interface ClarificationPendingEvent extends TurnEventBase {
  type: 'clarification_pending';
  question: string;
  tool_use_id: string;
  run_id: string;
  enable?: ModuleEnableOffer;
}

/** Approval gate halt-before: the held tool runs only after /resume approves. */
export interface ApprovalPendingEvent extends TurnEventBase {
  type: 'approval_pending';
  tool_name: string;
  summary: string;
  tool_use_id: string;
  run_id: string;
  policy: string | null;
}

/** A deferred tool parked the turn (AWAITING_TOOL_RESULT); the result arrives via /tool_result or a finished background job. */
export interface DeferredToolPendingEvent extends TurnEventBase {
  type: 'deferred_tool_pending';
  tool_name: string;
  deferred_id: string;
  tool_use_id: string;
  run_id: string;
}

/** A background job was queued for this turn. */
export interface JobSubmittedEvent extends TurnEventBase {
  type: 'job_submitted';
  job: JobRow;
}

/** A transient, non-fatal notice line (model busy, output cap hit). */
export interface NoticeEvent extends TurnEventBase {
  type: 'notice';
  text: string;
}

/** The turn was cancelled (Stop). */
export interface CancelledEvent extends TurnEventBase {
  type: 'cancelled';
  run_id: string;
  reason?: string | null;
}

/** The turn failed; `text` is the user-facing message. */
export interface ErrorEvent extends TurnEventBase {
  type: 'error';
  text: string;
  detail?: string;
}

/** Guide token usage for the turn (emitted before done). */
export interface UsageEvent extends TurnEventBase {
  type: 'usage';
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}

/** Terminal sentinel: the stream is complete. */
export interface DoneEvent extends TurnEventBase {
  type: 'done';
}

/** An end-of-turn reflection hook logged a context suggestion. */
export interface SuggestionLoggedEvent extends TurnEventBase {
  type: 'suggestion_logged';
  trigger: string;
  entity_type?: string | null;
}

/** Connect handshake for /api/notifications. */
export interface HelloEvent {
  type: 'hello';
}

/** An entity changed out-of-band (captions, revisions, promotions); the UI re-fetches. `reason` names the change; the optional keys carry the revision-chain specifics. */
export interface EntityUpdatedEvent {
  type: 'entity_updated';
  entity_id: string;
  reason: string;
  member_id?: string;
  attached_entity_id?: string;
  wasRevisionOf?: string;
  superseded?: string[];
  reanchored?: string[];
  deleted_revision?: string;
  re_parented_children?: unknown[];
  re_anchored_members?: unknown[];
  new_current?: string;
  restored?: string[];
}

/** Module install/state change (Settings → Modules toasts + live refresh). */
export interface ModuleEvent {
  type: 'module';
  id: string;
  title: string;
  state: string;
  progress?: string | null;
  error?: string | null;
}

/** Every event the per-turn chat stream can carry. */
export type SSEEvent =
  | ManifestEvent
  | DeltaEvent
  | ToolStartEvent
  | ToolProgressEvent
  | ToolChunkEvent
  | ToolResultEvent
  | EntityRegisteredEvent
  | PlanEvent
  | ClarificationPendingEvent
  | ApprovalPendingEvent
  | DeferredToolPendingEvent
  | JobSubmittedEvent
  | NoticeEvent
  | CancelledEvent
  | ErrorEvent
  | UsageEvent
  | DoneEvent
  | SuggestionLoggedEvent;

/** Every event the global /api/notifications stream can carry. */
export type NotificationEvent =
  | HelloEvent
  | EntityUpdatedEvent
  | ModuleEvent;
