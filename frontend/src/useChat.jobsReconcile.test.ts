/**
 * reconcileJobs — the /api/jobs poll must REMOVE a job that dropped out of the
 * server's response (dismissed → archived, or aged past the limit), or its row
 * lingers forever and "Dismiss" hangs at "dismissing…". Regression guard for
 * that live bug. It must still keep queued/running jobs a poll momentarily missed
 * (or that the SSE 'job_submitted' handler just added optimistically).
 */
import { describe, it, expect } from 'vitest'
import { reconcileJobs } from './useChat'

const J = (id: string, status: string, t = 1000) => ({ id, status, title: id, t })
const F = (id: string, status: string, created_at?: string) => ({ id, status, title: id, created_at })

describe('reconcileJobs', () => {
  it('removes a TERMINAL job that dropped out of fresh (dismiss → row disappears)', () => {
    const out = reconcileJobs([J('a', 'done'), J('b', 'done')], [F('a', 'done')])
    expect(out.map(j => j.id).sort()).toEqual(['a'])   // b was archived server-side → removed
  })

  it('keeps a queued/running job the poll momentarily missed (optimistic add / race)', () => {
    const out = reconcileJobs([J('a', 'done'), J('new', 'queued')], [F('a', 'done')])
    expect(out.map(j => j.id).sort()).toEqual(['a', 'new'])   // 'new' (active) kept
  })

  it('updates status from fresh and adds new jobs', () => {
    const out = reconcileJobs([J('a', 'running')], [F('a', 'done'), F('b', 'queued')])
    expect(out.find(j => j.id === 'a')!.status).toBe('done')
    expect(out.find(j => j.id === 'b')).toBeTruthy()
  })

  it('preserves existing t for sort stability', () => {
    const out = reconcileJobs([J('a', 'running', 777)], [F('a', 'done', '2026-01-01T00:00:00Z')])
    expect(out.find(j => j.id === 'a')!.t).toBe(777)
  })

  it('drops a running job only once it goes terminal AND leaves fresh (normal completion+archive)', () => {
    // was running & present → still kept; then it finishes+archives → absent+done → removed
    expect(reconcileJobs([J('x', 'running')], [F('x', 'running')]).map(j => j.id)).toEqual(['x'])
    expect(reconcileJobs([J('x', 'done')], []).map(j => j.id)).toEqual([])
  })
})
