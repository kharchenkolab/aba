/**
 * ondemand.md P6 — the HPC session card in the (i) Jobs tab. Polls
 * /api/hpc/session and shows where ABA itself runs: a Slurm node/cores/walltime
 * on a cluster, else the local CPU picture.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { HpcSessionCard } from './Drawer'

function mockFetch(data: unknown) {
  global.fetch = vi.fn(async () => ({ ok: true, json: async () => data })) as unknown as typeof fetch
}

describe('HpcSessionCard', () => {
  it('shows the Slurm node, partition + walltime-left on a cluster', async () => {
    mockFetch({ submitter: 'slurm', on_slurm: true, cores: 4, thread_cap: 4,
                slurm_job_id: '999', node: 'node09', time_left: '2:00:00',
                partition: 'short', alloc_cores: '4', alloc_mem: '8G' })
    const { container } = render(<HpcSessionCard />)
    await waitFor(() => expect(container.textContent).toContain('node09'))
    expect(container.textContent).toContain('Slurm')
    expect(container.textContent).toContain('short')
    expect(container.textContent).toContain('2:00:00')
    expect(container.textContent).toContain('job 999')
  })

  it('shows the local CPU picture off-cluster (cores + BLAS thread cap)', async () => {
    mockFetch({ submitter: 'local', on_slurm: false, cores: 56, thread_cap: 8 })
    const { container } = render(<HpcSessionCard />)
    await waitFor(() => expect(container.textContent).toContain('Local'))
    expect(container.textContent).toContain('56 cores')
    expect(container.textContent).toContain('8 BLAS threads')
  })
})
