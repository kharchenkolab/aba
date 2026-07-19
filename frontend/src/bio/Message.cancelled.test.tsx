/**
 * F4 (release soak, 2026-07-19): after Stop, the cancelled turn rendered as
 * the ErrorBoundary's "This message couldn't be displayed." banner — the
 * Message renderer THREW on the degenerate shapes a cancelled turn leaves
 * behind. Probe the shapes; none may throw.
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import Message from './Message'
import type { DisplayMessage, Block } from '../types'

const SHAPES: Array<[string, DisplayMessage]> = [
  ['empty blocks', { id: 'c1', role: 'assistant', blocks: [] }],
  ['null-ish text block',
   { id: 'c2', role: 'assistant',
     blocks: [{ type: 'text', text: undefined } as unknown as Block] }],
  ['tool_start with no result (cancel mid-tool)',
   { id: 'c3', role: 'assistant',
     blocks: [{ type: 'tool_start', name: 'run_python',
                input: { code: 'x' }, tool_use_id: 'tu_c' } as Block] }],
  ['tool_start with null input',
   { id: 'c4', role: 'assistant',
     blocks: [{ type: 'tool_start', name: 'run_python',
                input: null, tool_use_id: 'tu_d' } as unknown as Block] }],
  ['error block with no detail',
   { id: 'c5', role: 'assistant',
     blocks: [{ type: 'error', text: 'Cancelled' } as unknown as Block] }],
  ['cancelled error block with null text',
   { id: 'c6', role: 'assistant',
     blocks: [{ type: 'error', text: null } as unknown as Block] }],
  ['undefined blocks entirely',
   { id: 'c7', role: 'assistant' } as unknown as DisplayMessage],
]

describe('Message — cancelled-turn degenerate shapes must not throw (F4)', () => {
  for (const [label, msg] of SHAPES) {
    it(`renders without throwing: ${label}`, () => {
      expect(() => render(
        <Message message={msg} entities={[]} pinnedFigureIds={new Set()}
                 keptKeys={new Set()} />
      )).not.toThrow()
    })
  }
})
