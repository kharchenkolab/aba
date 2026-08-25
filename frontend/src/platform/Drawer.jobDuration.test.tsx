/**
 * Jobs card — a FINISHED job must not keep counting.
 *
 * Reported live 2026-08-25: "duration on the done background job keeps ticking
 * after the job is already labeled done."
 *
 * Root cause, two layers:
 *
 * 1. durationFmt falls back to Date.now() whenever finished_at is missing, with
 *    no regard for whether the job ENDED. A terminal job with no finish stamp
 *    renders a number that grows forever — a fallback that looks exactly like
 *    live data, which is the worst kind of wrong.
 *
 * 2. Drawer's refresh effect stops polling the instant the row leaves
 *    queued/running, so the cached detail is the one fetched up to 4s BEFORE
 *    completion — the copy whose finished_at is still null. The backend had
 *    correct stamps all along (checked in the field session's DB: a done job
 *    with created 03:48:11, started 03:51:14, finished 03:52:20); only the
 *    UI's snapshot was stale. The same staleness drops the final log_tail
 *    chunk, so an ended job can also look like it produced less output.
 *
 * This guards layer 1 — the honest-rendering half. Layer 2 is fixed by taking
 * one last detail fetch on the transition to terminal.
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { JobDetailPanel } from './Drawer'

const job = (status: string) => ({ id: 'job_x', status, title: 't', t: 1 } as any)
const detail = (over: Record<string, unknown>) => ({
  id: 'job_x', kind: 'run_r', title: 't', status: 'done',
  params: { code: '1+1' }, log_tail: 'ok', error: null,
  created_at: null, started_at: null, finished_at: null, ...over,
} as any)

const durationText = (c: HTMLElement) =>
  ([...c.querySelectorAll('span')].map(s => s.textContent || '')
    .find(t => /^duration /.test(t)) || '')

describe('JobDetailPanel — duration of a finished job', () => {
  it('DONE with both stamps: the real elapsed time, not now-minus-start', () => {
    const { container } = render(
      <JobDetailPanel job={job('done')} detail={detail({
        started_at: '2026-08-25T03:51:14.609113+00:00',
        finished_at: '2026-08-25T03:52:20.090445+00:00',
      })} loading={false} />)
    expect(durationText(container)).toBe('duration 1m 05s')
  })

  it('DONE but finished_at MISSING: must not invent a running clock', () => {
    // The stale-snapshot case the user actually saw. Anything derived from
    // Date.now() here keeps growing on a job that ended.
    const { container } = render(
      <JobDetailPanel job={job('done')} detail={detail({
        status: 'done',
        started_at: new Date(Date.now() - 900_000).toISOString(),
        finished_at: null,
      })} loading={false} />)
    expect(durationText(container)).toBe('')
  })

  it('RUNNING with no finish stamp: a live clock is CORRECT here', () => {
    // WIDE: the fallback must survive where it is honest — this is the case it
    // was written for, and the fix must not take it away.
    const { container } = render(
      <JobDetailPanel job={job('running')} detail={detail({
        status: 'running',
        started_at: new Date(Date.now() - 65_000).toISOString(),
        finished_at: null,
      })} loading={false} />)
    expect(durationText(container)).toMatch(/^duration 1m \d\ds$/)
  })
})
