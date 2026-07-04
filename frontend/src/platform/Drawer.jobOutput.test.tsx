/**
 * Jobs card — the Output pane affordance. Regression guard for the fix where a
 * QUEUED job (no run.log yet → empty log_tail) rendered no output section at all,
 * so it looked like the log feature was missing. The pane must now appear for
 * queued/running jobs with a live placeholder, show real log_tail when present,
 * and stay absent for a terminal job that genuinely produced nothing.
 */
import { describe, it, expect } from 'vitest'
import { render, within } from '@testing-library/react'
import { JobDetailPanel } from './Drawer'

const job = (status: string) => ({ id: 'job_x', status, title: 't', t: 1 } as any)
const detail = (over: Record<string, unknown>) => ({
  id: 'job_x', kind: 'run_python', title: 't', status: 'queued',
  params: { code: "print('hi')" }, log_tail: null, error: null,
  created_at: null, started_at: null, finished_at: null, ...over,
} as any)

describe('JobDetailPanel — Output pane', () => {
  it('QUEUED job with no output: shows the output pane + a live placeholder (not nothing)', () => {
    const { container } = render(
      <JobDetailPanel job={job('queued')} detail={detail({ status: 'queued' })} loading={false} />)
    expect(container.textContent).toContain('output')
    expect(container.textContent).toMatch(/queued.*no output until a node/i)
  })

  it('RUNNING job with no output yet: shows the pane + a waiting placeholder', () => {
    const { container } = render(
      <JobDetailPanel job={job('running')} detail={detail({ status: 'running' })} loading={false} />)
    expect(container.textContent).toContain('output')
    expect(container.textContent).toMatch(/waiting for the first output/i)
  })

  it('job WITH log_tail: renders the actual output content', () => {
    const { container } = render(
      <JobDetailPanel job={job('done')} detail={detail({ status: 'done', log_tail: 'edit_distance: 1' })} loading={false} />)
    expect(container.textContent).toContain('output')
    expect(container.textContent).toContain('edit_distance: 1')
  })

  it('terminal job with genuinely no I/O: no output pane, shows the hint', () => {
    const { container } = render(
      <JobDetailPanel job={job('done')} detail={detail({ status: 'done', params: {} })} loading={false} />)
    expect(container.querySelector('.jobs__section')).toBeNull()
    expect(container.textContent).toMatch(/no captured input\/output/i)
  })
})
