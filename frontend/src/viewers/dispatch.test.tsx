/**
 * Dispatch parity test: client-side `dispatchViewers()` should return
 * the same ordered viewer list as the legacy /api/viewers/for endpoint.
 *
 * We test by constructing a fake registry that mirrors a typical bio
 * setup, then dispatching for a handful of node shapes (image, csv,
 * markdown, entity-backed figure, etc.) and asserting the picked viewer.
 */
import { describe, it, expect } from 'vitest'
import { dispatchViewers } from './dispatch'
import type { FileNode, ViewerRegistryEntry } from './types'

// Subset of the production registry used by tests.
const REG: ViewerRegistryEntry[] = [
  { id: 'figure-canvas', mode: 'canvas', component: 'FigureView', open_external: null, label: 'figure-canvas',
    priority: 10, requires_consent: false, entity_types: ['figure'], extensions: [], mime_patterns: [],
    applies_any: false, max_size_kb: null },
  { id: 'image-canvas', mode: 'canvas', component: 'ImageCanvas', open_external: null, label: 'image-canvas',
    priority: 5, requires_consent: false, entity_types: [], extensions: ['.png', '.jpg'],
    mime_patterns: ['image/png', 'image/jpeg'], applies_any: false, max_size_kb: null },
  { id: 'markdown-canvas', mode: 'canvas', component: 'MarkdownCanvas', open_external: null, label: 'markdown-canvas',
    priority: 6, requires_consent: false, entity_types: [], extensions: ['.md', '.markdown'],
    mime_patterns: [], applies_any: false, max_size_kb: null },
  { id: 'csv-table', mode: 'canvas', component: 'TableViewer', open_external: null, label: 'csv-table',
    priority: 5, requires_consent: false, entity_types: [], extensions: ['.csv', '.tsv'],
    mime_patterns: [], applies_any: false, max_size_kb: null },
  { id: 'ai-summary', mode: 'modal', component: 'AISummaryModal', open_external: null, label: 'Ask Guide',
    priority: 0, requires_consent: true, entity_types: [], extensions: [], mime_patterns: [],
    applies_any: true, max_size_kb: null },
]

function node(partial: Partial<FileNode>): FileNode {
  return { kind: 'file', name: '', path: '', ...partial }
}

describe('dispatchViewers', () => {
  it('picks markdown-canvas for a .md file', () => {
    const v = dispatchViewers(node({ name: 'README.md', path: 'README.md' }), REG)
    expect(v[0].id).toBe('markdown-canvas')
    // ai-summary always applies → second
    expect(v.find(x => x.id === 'ai-summary')).toBeDefined()
  })

  it('picks csv-table for a .csv file', () => {
    const v = dispatchViewers(node({ name: 'data.csv', path: 'data.csv' }), REG)
    expect(v[0].id).toBe('csv-table')
  })

  it('picks image-canvas for a .png file', () => {
    const v = dispatchViewers(node({ name: 'umap.png', path: 'umap.png' }), REG)
    expect(v[0].id).toBe('image-canvas')
  })

  it('picks figure-canvas for an entity-backed figure (higher priority than image)', () => {
    const v = dispatchViewers(node({
      name: 'umap.png', path: 'umap.png',
      entity_type: 'figure', entity_id: 'fig_abc',
    }), REG)
    expect(v[0].id).toBe('figure-canvas')
    // image-canvas still applies (matches extension) — just lower priority
    expect(v[1].id).toBe('image-canvas')
  })

  it('ai-summary applies to everything', () => {
    const v = dispatchViewers(node({ name: 'mystery.xyz', path: 'mystery.xyz' }), REG)
    expect(v.map(x => x.id)).toContain('ai-summary')
  })

  it('returns empty + ai-summary for unknown extension', () => {
    const v = dispatchViewers(node({ name: 'foo.weird', path: 'foo.weird' }), REG)
    // Only ai-summary (applies_any) matches
    expect(v.length).toBe(1)
    expect(v[0].id).toBe('ai-summary')
  })

  it('matches MIME pattern when extension matches an inferred type', () => {
    const v = dispatchViewers(node({ name: 'a.png', path: 'a.png' }), REG)
    // image/png matches both the extension list AND the mime pattern;
    // dedup means image-canvas should appear exactly once.
    expect(v.filter(x => x.id === 'image-canvas').length).toBe(1)
  })
})
