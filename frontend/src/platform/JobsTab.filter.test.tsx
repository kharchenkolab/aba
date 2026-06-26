/**
 * Jobs tab — status filter pills (with counts) + text filter.
 *  - pills appear only when >1 status is present;
 *  - a pill per present status with its count; clicking filters the list;
 *  - the search box appears only when >3 jobs are present.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, within } from '@testing-library/react'
import { JobsTab } from './Drawer'
import type { JobInfo } from '../types'

// HpcSessionCard (rendered by JobsTab) polls /api/hpc/session — stub it.
beforeEach(() => {
  global.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ submitter: 'local', on_slurm: false, cores: 8 }) })) as unknown as typeof fetch
})

const mk = (id: string, status: string, title: string, t: number): JobInfo => ({ id, status, title, t })

describe('JobsTab — status pills + text filter', () => {
  it('hides the pills when only one status is present', () => {
    const jobs = [mk('a', 'done', 'one', 3), mk('b', 'done', 'two', 2)]
    const { container } = render(<JobsTab jobs={jobs} />)
    expect(container.querySelector('.jobs__pills')).toBeNull()
    expect(container.querySelector('.jobs__search')).toBeNull()  // ≤3 jobs
  })

  it('shows a pill per present status with counts, and filters on click', () => {
    const jobs = [
      mk('a', 'running', 'r1', 6), mk('b', 'running', 'r2', 5),
      mk('c', 'done', 'd1', 4), mk('d', 'done', 'd2', 3), mk('e', 'done', 'd3', 2),
      mk('f', 'failed', 'boom', 1),
    ]
    const { container } = render(<JobsTab jobs={jobs} />)
    const pills = container.querySelector('.jobs__pills')!
    expect(pills).not.toBeNull()
    const labels = Array.from(pills.querySelectorAll('.jobs__pill')).map(p => p.textContent)
    expect(labels).toEqual(['All 6', 'running 2', 'done 3', 'failed 1'])

    // click "done" → only the 3 done rows remain
    const donePill = within(pills as HTMLElement).getByText(/^done/).closest('button')!
    fireEvent.click(donePill)
    const rows = container.querySelectorAll('.jobs__row .jobs__title')
    expect(Array.from(rows).map(r => r.textContent)).toEqual(['d1', 'd2', 'd3'])
  })

  it('shows the search box when >3 jobs and filters by title', () => {
    const jobs = [
      mk('a', 'running', 'pbmc integrate', 4), mk('b', 'done', 'deseq2 sweep', 3),
      mk('c', 'done', 'pbmc qc', 2), mk('d', 'failed', 'scvi train', 1),
    ]
    const { container } = render(<JobsTab jobs={jobs} />)
    const search = container.querySelector('.jobs__search') as HTMLInputElement
    expect(search).not.toBeNull()
    fireEvent.change(search, { target: { value: 'pbmc' } })
    const rows = container.querySelectorAll('.jobs__row .jobs__title')
    expect(Array.from(rows).map(r => r.textContent).sort()).toEqual(['pbmc integrate', 'pbmc qc'])
  })
})
