/**
 * ExternalViewerActions gating: an entity focus card should offer an external
 * viewer only when a real external launcher matches the entity's artifact.
 * We test the pure decision (externalViewersFor) against a registry that
 * mirrors the bio setup (pagoda3 external launchers + AI modal/canvas viewers).
 */
import { describe, it, expect } from 'vitest'
import { externalViewersFor, entityToNode } from './ExternalViewerActions'
import type { Entity } from '../types'
import type { ViewerRegistryEntry } from '../viewers/types'

const REG: ViewerRegistryEntry[] = [
  { id: 'dataset-canvas', mode: 'canvas', component: 'DatasetView', open_external: null, label: 'dataset-canvas',
    priority: 10, requires_consent: false, entity_types: ['dataset'], extensions: [], mime_patterns: [],
    applies_any: false, max_size_kb: null },
  { id: 'csv-table', mode: 'canvas', component: 'TableViewer', open_external: null, label: 'csv-table',
    priority: 5, requires_consent: false, entity_types: [], extensions: ['.csv', '.tsv'],
    mime_patterns: [], applies_any: false, max_size_kb: null },
  { id: 'pagoda3-lstar', mode: 'external', component: null, open_external: 'pagoda3_launcher', label: 'Explore in pagoda3',
    priority: 9, requires_consent: false, entity_types: [], extensions: ['.lstar.zarr', '.lstar.zarr.zip'],
    mime_patterns: [], applies_any: false, max_size_kb: null },
  { id: 'pagoda3-anndata', mode: 'external', component: null, open_external: 'pagoda3_launcher', label: 'Explore in pagoda3',
    priority: 8, requires_consent: false, entity_types: [], extensions: ['.h5ad'],
    mime_patterns: [], applies_any: false, max_size_kb: null },
  // AI viewers are modal/canvas (not external) — must never be surfaced as launch buttons.
  { id: 'ai-summary', mode: 'modal', component: 'AISummaryModal', open_external: null, label: 'Ask Guide',
    priority: 0, requires_consent: true, entity_types: [], extensions: [], mime_patterns: [],
    applies_any: true, max_size_kb: null },
]

function entity(partial: Partial<Entity>): Entity {
  return {
    id: 'e1', type: 'dataset', title: '', status: 'ready', artifact_path: null,
    producing_params: null, parent_entity_id: null, scenario_of: null, metadata: null,
    tags: [], notes: null, pinned: false, exec_id: null, artifact_kind: null,
    artifact_idx: null, derivation: null, actor: null, deleted_at: null,
    created_at: '', updated_at: '', ...partial,
  } as Entity
}

describe('externalViewersFor', () => {
  it('offers pagoda3 for a .h5ad dataset', () => {
    const { viewers } = externalViewersFor(entity({ artifact_path: 'data/processed.h5ad' }), REG)
    expect(viewers.map(v => v.id)).toEqual(['pagoda3-anndata'])
  })

  it('offers pagoda3 for a native .lstar.zarr store', () => {
    const { viewers } = externalViewersFor(entity({ artifact_path: 'out/sample.lstar.zarr' }), REG)
    expect(viewers.map(v => v.id)).toEqual(['pagoda3-lstar'])
  })

  it('offers nothing for a .csv dataset (no external viewer) — component renders null', () => {
    const { viewers } = externalViewersFor(entity({ artifact_path: 'table.csv' }), REG)
    expect(viewers).toHaveLength(0)
  })

  it('never surfaces AI (modal/canvas) viewers as launch buttons', () => {
    const { viewers } = externalViewersFor(entity({ artifact_path: 'mystery.xyz' }), REG)
    expect(viewers).toHaveLength(0)
  })

  it('targets the entity (no tree path) so the launch uses ?entity=<id>', () => {
    const { node } = externalViewersFor(entity({ id: 'ds_9', artifact_path: 'a/b/c.h5ad' }), REG)
    expect(node.path).toBe('')
    expect(node.entity_id).toBe('ds_9')
    expect(node.name).toBe('c.h5ad')          // basename drives extension match
  })

  it('falls back to title for name when there is no artifact_path', () => {
    const n = entityToNode(entity({ artifact_path: null, title: 'My dataset' }))
    expect(n.name).toBe('My dataset')
    expect(n.artifact_path).toBeNull()
  })
})
