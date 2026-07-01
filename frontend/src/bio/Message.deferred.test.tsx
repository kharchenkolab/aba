/**
 * Fix #5 (2026-06-08): when a tool returns {deferred:true,job_id}, the
 * backend halts the turn in AWAITING_TOOL_RESULT — the spinner on the
 * tool_start chip needs to clear so the chat doesn't look frozen for
 * the whole job duration. The eventual tool_result (delivered by the
 * job-complete webhook) flips it back to the normal ✓/✗ state.
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import Message from './Message'
import type { DisplayMessage, Block } from '../types'

describe('Message — deferred tool (Fix #5)', () => {
  it('shows queued badge instead of spinner when block.deferred is true', () => {
    const blocks: Block[] = [
      {
        type: 'tool_start',
        name: 'run_r',
        input: { code: 'cat("hi")', background: true },
        tool_use_id: 'tu_1',
        deferred: true,
        deferredJobId: 'job_abc123',
      } as Block,
    ]
    const m: DisplayMessage = { id: 'm1', role: 'assistant', blocks }
    const { container } = render(
      <Message message={m} entities={[]} pinnedFigureIds={new Set()} keptKeys={new Set()} />
    )
    // No active spinner — the queued state must replace it.
    expect(container.querySelector('.tool-spinner')).toBeNull()
    // tool-line carries the --queued modifier.
    const line = container.querySelector('.tool-line--queued')
    expect(line).not.toBeNull()
    // Label mentions the job id so the user can correlate with the Queues panel.
    expect(line!.textContent || '').toContain('job_abc123')
  })

  it('shows spinner normally when not deferred (regression)', () => {
    const blocks: Block[] = [
      {
        type: 'tool_start',
        name: 'run_r',
        input: { code: 'cat("hi")' },
        tool_use_id: 'tu_2',
      } as Block,
    ]
    const m: DisplayMessage = { id: 'm2', role: 'assistant', blocks }
    const { container } = render(
      <Message message={m} entities={[]} pinnedFigureIds={new Set()} keptKeys={new Set()} />
    )
    expect(container.querySelector('.tool-spinner')).not.toBeNull()
    expect(container.querySelector('.tool-line--queued')).toBeNull()
  })

  it('switches to ✓ when a tool_result arrives after deferred (webhook resolution)', () => {
    const blocks: Block[] = [
      {
        type: 'tool_start',
        name: 'run_r',
        input: { code: 'cat("hi")', background: true },
        tool_use_id: 'tu_3',
        deferred: true,
        deferredJobId: 'job_xyz',
      } as Block,
      {
        type: 'tool_result',
        name: 'run_r',
        result: { stdout: 'done', returncode: 0 },
        tool_use_id: 'tu_3',
      } as Block,
    ]
    const m: DisplayMessage = { id: 'm3', role: 'assistant', blocks }
    const { container } = render(
      <Message message={m} entities={[]} pinnedFigureIds={new Set()} keptKeys={new Set()} />
    )
    // done state wins over deferred — Result block resolves the chip.
    expect(container.querySelector('.tool-line--done')).not.toBeNull()
    expect(container.querySelector('.tool-line--queued')).toBeNull()
    expect(container.querySelector('.tool-spinner')).toBeNull()
  })

  it('run_nextflow with no result shows backgrounded badge, not an infinite spinner (reload fidelity)', () => {
    // After a reload the deferred_tool_pending SSE flag is gone; run_nextflow is
    // always backgrounded, so a resultless one must NOT render the running spinner.
    const blocks: Block[] = [
      {
        type: 'tool_start',
        name: 'run_nextflow',
        input: { pipeline: 'nf-core/rnaseq' },
        tool_use_id: 'tu_nf',
        // note: no `deferred` flag (simulates post-reload), no tool_result
      } as Block,
    ]
    const m: DisplayMessage = { id: 'm4', role: 'assistant', blocks }
    const { container } = render(
      <Message message={m} entities={[]} pinnedFigureIds={new Set()} keptKeys={new Set()} />
    )
    expect(container.querySelector('.tool-line--queued')).not.toBeNull()
    expect(container.querySelector('.tool-spinner')).toBeNull()
  })
})
