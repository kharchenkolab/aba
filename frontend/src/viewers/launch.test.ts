import { describe, it, expect, vi, afterEach } from 'vitest'
import { launchExternal } from './launch'
import type { FileNode, ViewerInfo } from './types'

const viewer = { id: 'pagoda3-lstar', mode: 'external', label: 'Explore in pagoda3' } as ViewerInfo

afterEach(() => vi.restoreAllMocks())

describe('launchExternal', () => {
  it('opens the ABA loading tab with viewer + path + project + label', () => {
    window.history.pushState({}, '', '/p/prj_abc/files')
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)

    launchExternal({ kind: 'file', name: 'x.lstar.zarr', path: 'work/x.lstar.zarr' } as FileNode, viewer)

    expect(openSpy).toHaveBeenCalled()
    const url = String(openSpy.mock.calls[0][0])
    expect(url.startsWith('/viewer-launch?')).toBe(true)
    const qs = new URLSearchParams(url.split('?')[1])
    expect(qs.get('viewer')).toBe('pagoda3-lstar')
    expect(qs.get('path')).toBe('work/x.lstar.zarr')
    expect(qs.get('project')).toBe('prj_abc')
    expect(qs.get('label')).toBe('Explore in pagoda3')
    expect(openSpy.mock.calls[0][1]).toBe('viewer-pagoda3-lstar')   // per-viewer tab name
  })

  it('sends entity when the node has no path', () => {
    window.history.pushState({}, '', '/p/prj_abc/files')
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)

    launchExternal({ kind: 'file', name: 'r', entity_id: 'res_1' } as FileNode, viewer)

    const qs = new URLSearchParams(String(openSpy.mock.calls[0][0]).split('?')[1])
    expect(qs.get('entity')).toBe('res_1')
    expect(qs.get('path')).toBeNull()
  })

  it('adds action=download (+ a distinct tab name) for the download variant', () => {
    window.history.pushState({}, '', '/p/prj_abc/files')
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)

    launchExternal({ kind: 'file', name: 'x.lstar.zarr', path: 'work/x.lstar.zarr' } as FileNode,
                   viewer, { action: 'download' })

    const qs = new URLSearchParams(String(openSpy.mock.calls[0][0]).split('?')[1])
    expect(qs.get('action')).toBe('download')
    expect(qs.get('viewer')).toBe('pagoda3-lstar')
    expect(openSpy.mock.calls[0][1]).toBe('viewer-pagoda3-lstar-dl')   // separate from the view tab
  })

  it('omits action for the default view variant', () => {
    window.history.pushState({}, '', '/p/prj_abc/files')
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)
    launchExternal({ kind: 'file', name: 'x.lstar.zarr', path: 'work/x.lstar.zarr' } as FileNode, viewer)
    const qs = new URLSearchParams(String(openSpy.mock.calls[0][0]).split('?')[1])
    expect(qs.get('action')).toBeNull()
  })
})
