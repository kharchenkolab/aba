/**
 * ProvenanceSection — the dataset-source display guard.
 *
 * A dataset imported by an agent (2026-07) carried its stated origin correctly
 * in `derivation` ({kind:'imported', source:'GEO:…'}) AND, because it was
 * registered mid-run, an `exec_id` — so the provenance endpoint returned a
 * Method (python) + Environment (scanpy). The panel then rendered the
 * registering run's method/env and showed NO source at all: the expanded grid
 * had no Origin row, and the collapsed summary was "via python…" / "assembled
 * by an agent", masking the import. These guards pin that the source shows.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { Entity } from '../types'

// The panel fetches Method/Environment/lineage from this; the origin comes from
// entity.derivation, not the fetch. Mock it to the masking shape: a real method
// + env + actor, exactly the case that used to hide the source.
vi.mock('../lib/api', () => ({
  getEntityProvenance: vi.fn().mockResolvedValue({
    inputs: [],
    method: { language: 'python', code: 'print(1)\n'.repeat(10), code_lines: 10 },
    environment: { language_version: '3.12.13', key_packages: [{ name: 'scanpy', version: '1.12.1' }] },
    attribution: { actor: 'agent:ana_x', created_at: '2026-07-23T11:11:00Z' },
    lineage: { upstream: [], downstream: [] },
  }),
}))

import ProvenanceSection from './ProvenanceSection'

const SRC = 'GEO:GSM5746259 (series GSE192391)'

function mk(derivation: unknown): Entity {
  return { id: 'dat_585294b5', type: 'dataset', title: 'GSM5746259',
           actor: 'agent:ana_x', metadata: {}, derivation } as unknown as Entity
}

describe('ProvenanceSection origin display', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows an imported source in the collapsed summary (not masked by the run)', async () => {
    render(<ProvenanceSection entity={mk({ kind: 'imported', source: SRC })} onFocus={() => {}} />)
    // The source appears even though the entity also has a python/scanpy method.
    expect(await screen.findByText(new RegExp(SRC.replace(/[()]/g, '\\$&')))).toBeTruthy()
  })

  it('shows an Origin row with the source when expanded', async () => {
    render(<ProvenanceSection entity={mk({ kind: 'imported', source: SRC })} onFocus={() => {}} />)
    fireEvent.click(await screen.findByText('Provenance'))
    await waitFor(() => expect(screen.getByText('Origin')).toBeTruthy())
    // Origin value carries the traceable source ref.
    const originVals = screen.getAllByText(new RegExp(SRC.replace(/[()]/g, '\\$&')))
    expect(originVals.length).toBeGreaterThan(0)
  })

  it('does NOT add an Origin row for a computed (exec) entity — redundant with Method', async () => {
    render(<ProvenanceSection entity={mk({ kind: 'exec' })} onFocus={() => {}} />)
    fireEvent.click(await screen.findByText('Provenance'))
    await waitFor(() => expect(screen.getByText('Method')).toBeTruthy())
    expect(screen.queryByText('Origin')).toBeNull()
  })
})
