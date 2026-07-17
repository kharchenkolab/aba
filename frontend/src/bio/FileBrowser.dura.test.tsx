/**
 * F1 render verify: FileBrowser shows a per-file durability pill driven by the
 * node's `state`/`badge` (from /api/runs/{id}/durable). Covers the Run-panel list
 * view (variant="wide") and the rail tree view. output_durability.md §6.2.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import FileBrowser, { type TreeNode } from './FileBrowser'

const tree: TreeNode = {
  kind: 'root', name: '', path: '', children: [
    { kind: 'file', name: 'umap.png', path: 'umap.png', size: 2048,
      artifact_path: '/api/runs/r/file?rel=umap.png', state: 'retained', badge: 'retained ✓' },
    { kind: 'file', name: 'big.h5ad', path: 'big.h5ad', size: 99_000_000,
      artifact_path: '/api/runs/r/file?rel=big.h5ad', state: 'saving',
      badge: 'saving… · keeps the version at run settlement', large: true },
    { kind: 'file', name: 'raw.h5ad', path: 'raw.h5ad', size: 99_000_000,
      artifact_path: '/api/runs/r/file?rel=raw.h5ad', state: 'at-risk',
      badge: 'at risk — large output on scratch, nothing has kept it yet', large: true },
    { kind: 'file', name: 'gone.dat', path: 'gone.dat', size: 10,
      artifact_path: null, state: 'cleared', badge: 'cleared' },
  ],
}

describe('FileBrowser durability pills', () => {
  it('renders a weft-truth state pill per file in the Run panel (list view)', () => {
    render(<FileBrowser root={tree} variant="wide" />)
    // retained → "retained ✓"
    expect(screen.getByText('retained ✓').className).toContain('files__badge--retained')
    // saving → short "saving…", full designed text in the tooltip
    const saving = screen.getByText('saving…')
    expect(saving.className).toContain('files__badge--saving')
    expect(saving.getAttribute('title')).toContain('keeps the version at run settlement')
    // at-risk → red pill (large output on scratch, nothing kept it)
    expect(screen.getByText('at risk').className).toContain('files__badge--at-risk')
    // cleared
    expect(screen.getByText('cleared').className).toContain('files__badge--cleared')
  })

  it('renders pills in the rail tree view too', () => {
    render(<FileBrowser root={tree} variant="rail" />)
    expect(screen.getByText('retained ✓')).toBeTruthy()
    expect(screen.getByText('saving…')).toBeTruthy()
  })

  it('shows no pill when a node has no durable state (project rail nodes)', () => {
    const plain: TreeNode = {
      kind: 'root', name: '', path: '', children: [
        { kind: 'file', name: 'readme.md', path: 'readme.md', size: 5 },
      ],
    }
    render(<FileBrowser root={plain} variant="wide" />)
    expect(screen.queryByText('retained ✓')).toBeNull()
    expect(document.querySelector('.files__badge--dura')).toBeNull()
  })

  it('offers Keep on not-yet-kept files (at-risk + in-sandbox), not on retained/cleared', () => {
    const onKeep = vi.fn()
    const t: TreeNode = {
      kind: 'root', name: '', path: '', children: [
        { kind: 'file', name: 'a.png', path: 'a.png', state: 'retained', badge: 'retained ✓',
          artifact_path: '/api/runs/r/file?rel=a.png' },
        { kind: 'file', name: 'b.h5ad', path: 'b.h5ad', state: 'at-risk', badge: 'at risk' },
        { kind: 'file', name: 'c.csv', path: 'c.csv', state: 'in-sandbox', badge: 'in sandbox' },
        { kind: 'file', name: 'd.dat', path: 'd.dat', state: 'cleared', badge: 'cleared' },
      ],
    }
    render(<FileBrowser root={t} variant="wide" actions={{ onKeep }} />)
    // Keep appears twice — the at-risk and in-sandbox files (not retained, not cleared)
    const keep = screen.getAllByLabelText('Keep file')
    expect(keep.length).toBe(2)
    fireEvent.click(keep[0])
    expect(onKeep).toHaveBeenCalledTimes(1)
    expect(['b.h5ad', 'c.csv']).toContain(onKeep.mock.calls[0][0].path)
  })
})
