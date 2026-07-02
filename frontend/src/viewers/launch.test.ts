import { describe, it, expect, vi, afterEach } from 'vitest'
import { launchExternal } from './launch'
import type { FileNode, ViewerInfo } from './types'

const node = { kind: 'file', name: 'x.lstar.zarr', path: 'work/x.lstar.zarr' } as FileNode
const viewer = { id: 'pagoda3-lstar', mode: 'external', label: 'Explore in pagoda3' } as ViewerInfo

afterEach(() => { vi.restoreAllMocks() })

describe('launchExternal', () => {
  it('POSTs viewer_id + path and opens the returned url', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ url: '/pagoda3/?store=s', prepare_job_id: null, label: null }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)

    const res = await launchExternal(node, viewer)

    expect(fetchMock).toHaveBeenCalledWith('/api/viewers/launch', expect.objectContaining({ method: 'POST' }))
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body).toMatchObject({ viewer_id: 'pagoda3-lstar', path: 'work/x.lstar.zarr' })
    expect(openSpy).toHaveBeenCalled()
    expect(openSpy.mock.calls[0][0]).toContain('/pagoda3/?store=s')
    expect(res.url).toBe('/pagoda3/?store=s')
  })

  it('sends entity_id when the node has no path', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ url: '/v/', prepare_job_id: null, label: null }) })
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(window, 'open').mockReturnValue(null)
    await launchExternal({ kind: 'file', name: 'r', entity_id: 'res_1' } as FileNode, viewer)
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body).toMatchObject({ viewer_id: 'pagoda3-lstar', entity_id: 'res_1' })
    expect(body.path).toBeUndefined()
  })

  it('throws on an error response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 501, json: async () => ({ detail: 'no launcher' }) }))
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)
    await expect(launchExternal(node, viewer)).rejects.toThrow('no launcher')
    expect(openSpy).not.toHaveBeenCalled()
  })
})
