/**
 * Jobs card — the Output pane affordance. Regression guard for the fix where a
 * QUEUED job (no run.log yet → empty log_tail) rendered no output section at all,
 * so it looked like the log feature was missing. The pane's toggle must now appear
 * for queued/running jobs (collapsed by default, like the code pane); expanding it
 * shows a live placeholder, real log_tail when present, and it stays absent only for
 * a terminal job that genuinely produced nothing.
 */
import { describe, it, expect } from 'vitest'
import { render, fireEvent, within } from '@testing-library/react'
import { JobDetailPanel } from './Drawer'

const job = (status: string) => ({ id: 'job_x', status, title: 't', t: 1 } as any)
const detail = (over: Record<string, unknown>) => ({
  id: 'job_x', kind: 'run_python', title: 't', status: 'queued',
  params: { code: "print('hi')" }, log_tail: null, error: null,
  created_at: null, started_at: null, finished_at: null, ...over,
} as any)

// The output section's toggle button (collapsed by default). Returns the button, or null.
const outputToggle = (c: HTMLElement) =>
  [...c.querySelectorAll('.jobs__toggle')].find(b => /output/.test(b.textContent || '')) as HTMLElement | undefined

describe('JobDetailPanel — Output pane', () => {
  it('QUEUED job: output toggle present + COLLAPSED by default; expand → live placeholder', () => {
    const { container } = render(
      <JobDetailPanel job={job('queued')} detail={detail({ status: 'queued' })} loading={false} />)
    const btn = outputToggle(container)
    expect(btn).toBeTruthy()                                   // affordance exists
    expect(container.textContent).not.toMatch(/no output until a node/i)  // collapsed → content hidden
    fireEvent.click(btn!)
    expect(container.textContent).toMatch(/queued.*no output until a node/i)  // expanded → placeholder
  })

  it('RUNNING job: expand → waiting placeholder', () => {
    const { container } = render(
      <JobDetailPanel job={job('running')} detail={detail({ status: 'running' })} loading={false} />)
    fireEvent.click(outputToggle(container)!)
    expect(container.textContent).toMatch(/waiting for the first output/i)
  })

  it('job WITH log_tail: expand → real output content', () => {
    const { container } = render(
      <JobDetailPanel job={job('done')} detail={detail({ status: 'done', log_tail: 'edit_distance: 1' })} loading={false} />)
    fireEvent.click(outputToggle(container)!)
    expect(container.textContent).toContain('edit_distance: 1')
  })

  it('terminal job with genuinely no I/O: no output pane, shows the hint', () => {
    const { container } = render(
      <JobDetailPanel job={job('done')} detail={detail({ status: 'done', params: {} })} loading={false} />)
    expect(outputToggle(container)).toBeFalsy()
    expect(container.textContent).toMatch(/no captured input\/output/i)
  })
})

describe('JobDetailPanel — Dismiss vs Cancel', () => {
  const btns = (c: HTMLElement) => [...c.querySelectorAll('button')].map(b => b.textContent || '').join('|')
  const dismissBtn = (c: HTMLElement) =>
    [...c.querySelectorAll('button')].find(b => /Dismiss/.test(b.textContent || '')) as HTMLElement | undefined

  it('terminal job → Dismiss, not Cancel', () => {
    const { container } = render(<JobDetailPanel job={job('done')} detail={detail({ status: 'done' })} loading={false} />)
    expect(btns(container)).toMatch(/Dismiss/)
    expect(btns(container)).not.toMatch(/Cancel job/)
  })

  it('running job → Cancel, not Dismiss', () => {
    const { container } = render(<JobDetailPanel job={job('running')} detail={detail({ status: 'running' })} loading={false} />)
    expect(btns(container)).toMatch(/Cancel job/)
    expect(btns(container)).not.toMatch(/Dismiss/)
  })

  it('Dismiss POSTs to /api/jobs/{id}/archive', () => {
    const calls: string[] = []
    const orig = global.fetch
    global.fetch = ((u: unknown, o: { method?: string } = {}) => {
      calls.push(`${String(u)} ${o.method || 'GET'}`)
      return Promise.resolve({ ok: true }) as unknown as Promise<Response>
    }) as unknown as typeof fetch
    try {
      const { container } = render(<JobDetailPanel job={job('failed')} detail={detail({ status: 'failed' })} loading={false} />)
      fireEvent.click(dismissBtn(container)!)
    } finally { global.fetch = orig }
    expect(calls.some(c => /\/api\/jobs\/job_x\/archive POST/.test(c))).toBe(true)
  })
})
