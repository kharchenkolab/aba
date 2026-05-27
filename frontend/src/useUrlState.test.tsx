/**
 * URL ↔ state round-trip tests for the routing hook (MemoryRouter).
 *
 * URL grammar (Option 3): section is NOT in the URL — rail tab is local
 * state in App.tsx. The URL only carries content-changing state
 * (project, thread, entity, scene, file path).
 *
 * Legacy URLs with a section segment are tolerated: the section is
 * consumed and dropped during parse, so old Phase-2 bookmarks still land
 * on the right entity.
 */
import { describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { useUrlState } from './useUrlState'

function wrap(initialPath: string) {
  return ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter initialEntries={[initialPath]}>{children}</MemoryRouter>
  )
}

function useUrlAndPath() {
  const url = useUrlState()
  const location = useLocation()
  const navigate = useNavigate()
  return { url, pathname: location.pathname, back: () => navigate(-1) }
}

describe('useUrlState — parsing', () => {
  it('parses root as home', () => {
    const { result } = renderHook(() => useUrlState(), { wrapper: wrap('/') })
    expect(result.current.pid).toBe(null)
    expect(result.current.isHome).toBe(true)
    expect(result.current.threadId).toBe('default')
    expect(result.current.focusedId).toBe('workspace')
    expect(result.current.filePath).toBe('')
  })

  it('parses bare project URL', () => {
    const { result } = renderHook(() => useUrlState(), { wrapper: wrap('/p/X') })
    expect(result.current.pid).toBe('X')
    expect(result.current.scene).toBe(null)
  })

  it('parses thread + entity', () => {
    const { result } = renderHook(() => useUrlState(), {
      wrapper: wrap('/p/X/t/T/e/E'),
    })
    expect(result.current.threadId).toBe('T')
    expect(result.current.focusedId).toBe('E')
  })

  it('parses files + path', () => {
    const { result } = renderHook(() => useUrlState(), {
      wrapper: wrap('/p/X/files/threads/01_foo/README.md'),
    })
    expect(result.current.filePath).toBe('threads/01_foo/README.md')
    expect(result.current.focusedId).toBe('workspace')
  })

  it('parses scenes', () => {
    const a = renderHook(() => useUrlState(), { wrapper: wrap('/p/X/overview') })
    expect(a.result.current.scene).toBe('overview')

    const b = renderHook(() => useUrlState(), { wrapper: wrap('/p/X/t/T/inventory') })
    expect(b.result.current.scene).toBe('inventory')
    expect(b.result.current.threadId).toBe('T')
  })

  it('tolerates legacy /<section>/ URLs by dropping the section', () => {
    // Legacy Phase-2 URLs that included a tab segment should still parse
    // correctly — section dropped, rest preserved.
    const cases: [string, { tid: string; eid: string }][] = [
      ['/p/X/runs',           { tid: 'default', eid: 'workspace' }],
      ['/p/X/claims/t/T',     { tid: 'T',       eid: 'workspace' }],
      ['/p/X/data/e/Y',       { tid: 'default', eid: 'Y'         }],
      ['/p/X/results/t/T/e/Y',{ tid: 'T',       eid: 'Y'         }],
    ]
    for (const [path, want] of cases) {
      const { result } = renderHook(() => useUrlState(), { wrapper: wrap(path) })
      expect(result.current.threadId).toBe(want.tid)
      expect(result.current.focusedId).toBe(want.eid)
    }
  })
})

describe('useUrlState — setters write URLs and the hook reflects them', () => {
  it('setFocus preserves thread', () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/t/T') })
    act(() => result.current.url.setFocus('E'))
    expect(result.current.pathname).toBe('/p/X/t/T/e/E')
    expect(result.current.url.threadId).toBe('T')
    expect(result.current.url.focusedId).toBe('E')
  })

  it('setFocus drops active scene', () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/t/T/inventory') })
    expect(result.current.url.scene).toBe('inventory')
    act(() => result.current.url.setFocus('E'))
    expect(result.current.url.scene).toBe(null)
    expect(result.current.pathname).toBe('/p/X/t/T/e/E')
  })

  it('setThread clears focus and scene', () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/t/T1/e/E') })
    act(() => result.current.url.setThread('T2'))
    expect(result.current.pathname).toBe('/p/X/t/T2')
    expect(result.current.url.focusedId).toBe('workspace')
  })

  it('setFilePath opens a file (any pid, no t/e/scene needed)', () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/t/T/e/E') })
    act(() => result.current.url.setFilePath('threads/01_foo/README.md'))
    expect(result.current.pathname).toBe('/p/X/files/threads/01_foo/README.md')
    expect(result.current.url.filePath).toBe('threads/01_foo/README.md')
    // Opening a file drops thread + focus + scene
    expect(result.current.url.threadId).toBe('default')
    expect(result.current.url.focusedId).toBe('workspace')
  })

  it('setFilePath("") closes the file', () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/files/some.csv') })
    act(() => result.current.url.setFilePath(''))
    expect(result.current.pathname).toBe('/p/X')
    expect(result.current.url.filePath).toBe('')
  })

  it('setScene toggles inventory / overview / off', () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/t/T') })
    act(() => result.current.url.setScene('inventory'))
    expect(result.current.pathname).toBe('/p/X/t/T/inventory')
    act(() => result.current.url.setScene(null))
    expect(result.current.pathname).toBe('/p/X/t/T')
  })

  it('setNav combines multiple URL pieces in one navigation', () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X') })
    act(() => result.current.url.setNav({ threadId: 'T', focusedId: 'workspace', scene: 'inventory' }))
    expect(result.current.pathname).toBe('/p/X/t/T/inventory')
  })
})

describe('useUrlState — Back / Forward via MemoryRouter history', () => {
  it('Back from /p/X/t/T to /p/X updates the hook', async () => {
    const { result } = renderHook(useUrlAndPath, {
      wrapper: ({ children }) => (
        <MemoryRouter initialEntries={['/p/X', '/p/X/t/T']} initialIndex={1}>
          {children}
        </MemoryRouter>
      ),
    })
    expect(result.current.pathname).toBe('/p/X/t/T')
    expect(result.current.url.threadId).toBe('T')

    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X')
    expect(result.current.url.threadId).toBe('default')
  })

  it('Back from /p/X/t/T/e/Y to /p/X/t/T clears focus, keeps thread', async () => {
    const { result } = renderHook(useUrlAndPath, {
      wrapper: ({ children }) => (
        <MemoryRouter initialEntries={['/p/X/t/T', '/p/X/t/T/e/Y']} initialIndex={1}>
          {children}
        </MemoryRouter>
      ),
    })
    expect(result.current.url.focusedId).toBe('Y')

    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X/t/T')
    expect(result.current.url.focusedId).toBe('workspace')
    expect(result.current.url.threadId).toBe('T')
  })

  it('Back from /p/X/files/foo.csv to /p/X clears the file', async () => {
    const { result } = renderHook(useUrlAndPath, {
      wrapper: ({ children }) => (
        <MemoryRouter initialEntries={['/p/X', '/p/X/files/foo.csv']} initialIndex={1}>
          {children}
        </MemoryRouter>
      ),
    })
    expect(result.current.url.filePath).toBe('foo.csv')

    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X')
    expect(result.current.url.filePath).toBe('')
  })
})
