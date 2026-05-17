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

export type SSEEvent = DeltaEvent | ToolStartEvent | ToolResultEvent | DoneEvent | ErrorEvent
