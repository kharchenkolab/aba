/**
 * F1 render verify: FileBrowser shows a per-file durability pill driven by the
 * node's `state`/`badge` (from /api/runs/{id}/durable), in the §8c two-axis
 * vocabulary (kept ✓ / keeping… / temporary / discarded, + location only when
 * not simply here). Covers the Run-panel list view (variant="wide") and the
 * rail tree view. output_durability.md §6.2 + more_weft_ui.md §8c.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import FileBrowser, { type TreeNode } from './FileBrowser'

const tree: TreeNode = {
  kind: 'root', name: '', path: '', children: [
    { kind: 'file', name: 'report.png', path: 'report.png', size: 2048,
      artifact_path: '/api/runs/r/file?rel=report.png', state: 'retained', badge: 'kept ✓' },
    { kind: 'file', name: 'model.bin', path: 'model.bin', size: 99_000_000,
      artifact_path: '/api/runs/r/file?rel=model.bin', state: 'saving',
      badge: 'keeping… · keeps the version at run settlement', large: true },
    { kind: 'file', name: 'stage2.parquet', path: 'stage2.parquet', size: 99_000_000,
      artifact_path: '/api/runs/r/file?rel=stage2.parquet', state: 'at-risk',
      badge: 'temporary — will be discarded; Keep it to save it', large: true },
    { kind: 'file', name: 'gone.dat', path: 'gone.dat', size: 10,
      artifact_path: null, state: 'cleared', badge: 'discarded — swept by housekeeping' },
  ],
}

describe('FileBrowser durability pills (§8c vocabulary)', () => {
  it('renders a protection-axis pill per file in the Run panel (list view)', () => {
    render(<FileBrowser root={tree} variant="wide" />)
    expect(screen.getByText('kept ✓').className).toContain('files__badge--retained')
    // saving → short "keeping…", full designed text in the tooltip
    const keeping = screen.getByText('keeping…')
    expect(keeping.className).toContain('files__badge--saving')
    expect(keeping.getAttribute('title')).toContain('keeps the version at run settlement')
    // unkept after close → "temporary" (at-risk keeps its red styling key)
    expect(screen.getByText('temporary').className).toContain('files__badge--at-risk')
    expect(screen.getByText('discarded').className).toContain('files__badge--cleared')
  })

  it('adds the location axis only when the bytes are not simply here', () => {
    const t: TreeNode = {
      kind: 'root', name: '', path: '', children: [
        { kind: 'file', name: 'far.dat', path: 'far.dat', state: 'retained',
          badge: 'kept ✓ · on siteB', site: 'siteB' },
        { kind: 'file', name: 'near.png', path: 'near.png', state: 'retained',
          badge: 'kept ✓', site: 'local' },
      ],
    }
    render(<FileBrowser root={t} variant="wide" />)
    expect(screen.getByText('kept ✓ · on siteB')).toBeTruthy() // composite: safe AND not here
    expect(screen.getByText('kept ✓')).toBeTruthy()            // local → protection only
  })

  it('OPEN-run unkept files (empty badge) show NO pill — temporary by absence', () => {
    const t: TreeNode = {
      kind: 'root', name: '', path: '', children: [
        { kind: 'file', name: 'work.csv', path: 'work.csv', state: 'in-sandbox', badge: '' },
        { kind: 'file', name: 'big.bin', path: 'big.bin', state: 'at-risk', badge: '', large: true },
      ],
    }
    render(<FileBrowser root={t} variant="wide" />)
    expect(screen.queryByText('temporary')).toBeNull()
    expect(document.querySelector('.files__badge--dura')).toBeNull()
  })

  it('renders pills in the rail tree view too', () => {
    render(<FileBrowser root={tree} variant="rail" />)
    expect(screen.getByText('kept ✓')).toBeTruthy()
    expect(screen.getByText('keeping…')).toBeTruthy()
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

  it('offers Keep on not-yet-kept files (at-risk + in-sandbox), not on retained/cleared', () => {
    const onKeep = vi.fn()
    const t: TreeNode = {
      kind: 'root', name: '', path: '', children: [
        { kind: 'file', name: 'a.png', path: 'a.png', state: 'retained', badge: 'kept ✓',
          artifact_path: '/api/runs/r/file?rel=a.png' },
        { kind: 'file', name: 'b.dat', path: 'b.dat', state: 'at-risk', badge: 'temporary' },
        { kind: 'file', name: 'c.csv', path: 'c.csv', state: 'in-sandbox', badge: 'temporary' },
        { kind: 'file', name: 'd.dat', path: 'd.dat', state: 'cleared', badge: 'discarded' },
      ],
    }
    render(<FileBrowser root={t} variant="wide" actions={{ onKeep }} />)
    // Keep appears twice — the at-risk and in-sandbox files (not retained, not cleared)
    const keep = screen.getAllByLabelText('Keep file')
    expect(keep.length).toBe(2)
    fireEvent.click(keep[0])
    expect(onKeep).toHaveBeenCalledTimes(1)
    expect(['b.dat', 'c.csv']).toContain(onKeep.mock.calls[0][0].path)
  })
})
