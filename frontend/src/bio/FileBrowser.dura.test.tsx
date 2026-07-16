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
      artifact_path: '/artifacts/p/x.png', state: 'kept', badge: 'kept ✓' },
    { kind: 'file', name: 'big.h5ad', path: 'big.h5ad', size: 99_000_000,
      artifact_path: '/api/runs/r/file?rel=big.h5ad', state: 'pinned-pending',
      badge: 'large · keeps the version at run settlement', large: true },
    { kind: 'file', name: 'gone.dat', path: 'gone.dat', size: 10,
      artifact_path: null, state: 'cleared', badge: 'cleared' },
  ],
}

describe('FileBrowser durability pills', () => {
  it('renders a state pill per file in the Run panel (list view)', () => {
    render(<FileBrowser root={tree} variant="wide" />)
    // kept → "kept ✓"
    expect(screen.getByText('kept ✓').className).toContain('files__badge--kept')
    // pinned-pending → short "pending", full designed text in the tooltip
    const pending = screen.getByText('pending')
    expect(pending.className).toContain('files__badge--pinned-pending')
    expect(pending.getAttribute('title')).toContain('keeps the version at run settlement')
    // cleared
    expect(screen.getByText('cleared').className).toContain('files__badge--cleared')
  })

  it('renders pills in the rail tree view too', () => {
    render(<FileBrowser root={tree} variant="rail" />)
    expect(screen.getByText('kept ✓')).toBeTruthy()
    expect(screen.getByText('pending')).toBeTruthy()
  })

  it('shows no pill when a node has no durable state (project rail nodes)', () => {
    const plain: TreeNode = {
      kind: 'root', name: '', path: '', children: [
        { kind: 'file', name: 'readme.md', path: 'readme.md', size: 5 },
      ],
    }
    render(<FileBrowser root={plain} variant="wide" />)
    expect(screen.queryByText('kept ✓')).toBeNull()
    expect(document.querySelector('.files__badge--dura')).toBeNull()
  })

  it('offers Keep only on in-sandbox files and fires onKeep with the node', () => {
    const onKeep = vi.fn()
    const t: TreeNode = {
      kind: 'root', name: '', path: '', children: [
        { kind: 'file', name: 'a.png', path: 'a.png', state: 'kept', badge: 'kept ✓',
          artifact_path: '/artifacts/x.png' },
        { kind: 'file', name: 'b.h5ad', path: 'b.h5ad', state: 'in-sandbox', badge: 'in sandbox' },
        { kind: 'file', name: 'c.dat', path: 'c.dat', state: 'cleared', badge: 'cleared' },
      ],
    }
    render(<FileBrowser root={t} variant="wide" actions={{ onKeep }} />)
    // Keep appears once — only for the in-sandbox file (not kept, not cleared)
    const keep = screen.getAllByLabelText('Keep file')
    expect(keep.length).toBe(1)
    fireEvent.click(keep[0])
    expect(onKeep).toHaveBeenCalledTimes(1)
    expect(onKeep.mock.calls[0][0].path).toBe('b.h5ad')
  })
})
