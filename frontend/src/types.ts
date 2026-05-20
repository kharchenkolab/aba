export type Role = 'user' | 'assistant'

export interface TextBlock  { type: 'text';   text: string }
export interface ImageBlock { type: 'image';  url: string; alt?: string }
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

export type Block = TextBlock | ImageBlock | ToolStartBlock | ToolResultBlock

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
export interface ErrorEvent       { type: 'error';       text: string }

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
