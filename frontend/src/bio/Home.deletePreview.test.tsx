/**
 * Delete-confirm consequence line — UX half of the project reclaim contract
 * (backend: core/compute/reclaim.py, GET /api/projects/{pid}/delete-preview,
 * backend/tests/test_project_delete_reclaims.py).
 *
 * "Delete project" used to free the two session prefixes and silently leave
 * every named/isolated env on disk. Now it reclaims what only this project
 * held — so the card must say how much, and must NOT imply it will touch the
 * envs other projects share.
 *
 * The ceiling matters as much as the claim: a preview that fails to load, or
 * an offline substrate that cannot assess an env, must degrade to the plain
 * card. The delete itself is never blocked on this number.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import Home from './Home'

const PROJECTS = [
  { id: 'prj_a', name: 'Alpha', created_at: '2026-01-01', last_touched: '2026-01-02',
    current: true, counts: {} },
  { id: 'prj_b', name: 'Beta', created_at: '2026-01-01', last_touched: '2026-01-01',
    current: false, counts: {} },
]

function installFetch(preview: unknown | null) {
  globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/delete-preview')) {
      return preview === null
        ? Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve(null) })
        : Promise.resolve({ ok: true, json: () => Promise.resolve(preview) })
    }
    if (url === '/api/projects') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(PROJECTS) })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(null) })
  }) as unknown as typeof fetch
}

async function openDeleteModal() {
  await act(async () => { render(<Home onEnter={() => {}} />) })
  // the ⋯ menu on the non-current project, then "Delete project"
  const menus = screen.getAllByTitle('Project actions')
  await act(async () => { fireEvent.click(menus[menus.length - 1]) })
  await act(async () => { fireEvent.click(screen.getByText('Delete project')) })
}

describe('project delete — consequence line', () => {
  beforeEach(() => { vi.restoreAllMocks() })
  afterEach(() => { vi.restoreAllMocks() })

  it('names the bytes only this project holds, and what is kept', async () => {
    installFetch({
      reclaimable_bytes: 3_355_443_200,
      rebuildable: [{ name: 'env-one' }, { name: 'env-two' }],
      shared: [{ name: 'env-shared' }],
      unknown: [],
    })
    await openDeleteModal()
    const body = document.body.textContent ?? ''
    expect(body).toMatch(/Frees about 3\.13 GB/)
    expect(body).toMatch(/2 environments only this project uses/)
    expect(body).toMatch(/1 shared with other projects is kept/)
  })

  it('says so when there is nothing to reclaim', async () => {
    installFetch({ reclaimable_bytes: 0, rebuildable: [], shared: [], unknown: [] })
    await openDeleteModal()
    expect(document.body.textContent ?? '')
      .toMatch(/No environment storage to reclaim/)
  })

  it('surfaces what could not be assessed rather than promising it', async () => {
    installFetch({
      reclaimable_bytes: 0, rebuildable: [], shared: [],
      unknown: [{ name: 'env-unknowable' }],
    })
    await openDeleteModal()
    expect(document.body.textContent ?? '')
      .toMatch(/1 could not be assessed .*will be left alone/)
  })

  it('degrades to the plain card when the preview cannot load', async () => {
    installFetch(null)
    await openDeleteModal()
    const body = document.body.textContent ?? ''
    expect(body).toMatch(/This permanently removes the project/)   // card renders
    expect(body).not.toMatch(/Frees about/)
    expect(screen.getByRole('button', { name: 'Delete project' })).toBeTruthy()
  })
})
