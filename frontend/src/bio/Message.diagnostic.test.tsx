/**
 * Friction-fix B: a structured-tool error (e.g. ensure_capability) carries
 * `note` + `diagnostic` instead of stdout/stderr. Before the fix `finalOut` was
 * empty for these, so the failed chip had NO output toggle — the user saw
 * "✗ error" with no way to read what broke. Now the diagnostic is viewable.
 */
import { describe, it, expect } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import Message from './Message'
import type { DisplayMessage, Block } from '../types'

describe('Message — error diagnostic surfacing (friction B)', () => {
  it('shows an output toggle + the real diagnostic for a structured ensure_capability error', () => {
    const blocks: Block[] = [
      { type: 'tool_start', name: 'ensure_capability',
        input: { name: 'pagoda2' }, tool_use_id: 'tu_e' } as Block,
      { type: 'tool_result', tool_use_id: 'tu_e',
        result: { status: 'error', note: "R install of 'pagoda2' failed.",
                  diagnostic: "installation of package 'hdf5r' had non-zero exit status" } } as Block,
    ]
    const m: DisplayMessage = { id: 'm1', role: 'assistant', blocks }
    const { container } = render(
      <Message message={m} entities={[]} pinnedFigureIds={new Set()} keptKeys={new Set()} />
    )
    // A structured error now has something to show → the output toggle appears.
    const btn = Array.from(container.querySelectorAll('button'))
      .find(b => /output/i.test(b.textContent || ''))
    expect(btn, 'expected an output toggle on the failed chip').toBeTruthy()
    // Expanding reveals the actual diagnostic (not the old C++-banner noise).
    fireEvent.click(btn!)
    const text = container.textContent || ''
    expect(text).toContain('hdf5r')
    expect(text).toContain('non-zero exit status')
  })
})
