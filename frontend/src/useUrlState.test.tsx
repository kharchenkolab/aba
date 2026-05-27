/**
 * URL ↔ state round-trip tests for the routing hook. Uses MemoryRouter so
 * we can drive history.push / pop programmatically and assert what the
 * hook returns + writes back.
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

/** Hook variant that also exposes the current pathname + the router's
 *  navigate(-1) so tests can drive Back without going through
 *  window.history (MemoryRouter has its own in-memory stack). */
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
    expect(result.current.section).toBe('threads')
    expect(result.current.threadId).toBe('default')
    expect(result.current.focusedId).toBe('workspace')
  })

  it('parses bare project URL', () => {
    const { result } = renderHook(() => useUrlState(), { wrapper: wrap('/p/X') })
    expect(result.current.pid).toBe('X')
    expect(result.current.section).toBe('threads')
    expect(result.current.scene).toBe(null)
  })

  it('parses section explicitly', () => {
    const { result } = renderHook(() => useUrlState(), { wrapper: wrap('/p/X/runs') })
    expect(result.current.section).toBe('runs')
    expect(result.current.focusedId).toBe('workspace')
  })

  it('parses section + thread + entity', () => {
    const { result } = renderHook(() => useUrlState(), {
      wrapper: wrap('/p/X/claims/t/T/e/E'),
    })
    expect(result.current.section).toBe('claims')
    expect(result.current.threadId).toBe('T')
    expect(result.current.focusedId).toBe('E')
  })

  it('parses files + path', () => {
    const { result } = renderHook(() => useUrlState(), {
      wrapper: wrap('/p/X/files/threads/01_foo/README.md'),
    })
    expect(result.current.section).toBe('files')
    expect(result.current.filePath).toBe('threads/01_foo/README.md')
  })

  it('parses scenes', () => {
    const a = renderHook(() => useUrlState(), { wrapper: wrap('/p/X/overview') })
    expect(a.result.current.scene).toBe('overview')

    const b = renderHook(() => useUrlState(), { wrapper: wrap('/p/X/t/T/inventory') })
    expect(b.result.current.scene).toBe('inventory')
    expect(b.result.current.threadId).toBe('T')
  })

  it('parses legacy /t/<tid> with default section', () => {
    const { result } = renderHook(() => useUrlState(), { wrapper: wrap('/p/X/t/T') })
    expect(result.current.section).toBe('threads')
    expect(result.current.threadId).toBe('T')
  })
})

describe('useUrlState — setters write URLs and the hook reflects them', () => {
  it('setSection navigates and re-reads', () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X') })
    expect(result.current.pathname).toBe('/p/X')
    expect(result.current.url.section).toBe('threads')

    act(() => result.current.url.setSection('runs'))
    expect(result.current.pathname).toBe('/p/X/runs')
    expect(result.current.url.section).toBe('runs')

    act(() => result.current.url.setSection('claims'))
    expect(result.current.pathname).toBe('/p/X/claims')
    expect(result.current.url.section).toBe('claims')

    // Switching back to the default section drops the segment.
    act(() => result.current.url.setSection('threads'))
    expect(result.current.pathname).toBe('/p/X')
  })

  it('setFocus preserves section + thread', () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/runs/t/T') })
    act(() => result.current.url.setFocus('E'))
    expect(result.current.pathname).toBe('/p/X/runs/t/T/e/E')
    expect(result.current.url.section).toBe('runs')
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
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/runs/t/T1/e/E') })
    act(() => result.current.url.setThread('T2'))
    expect(result.current.pathname).toBe('/p/X/runs/t/T2')
    expect(result.current.url.focusedId).toBe('workspace')
  })

  it('setFilePath puts us into files section', () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/claims') })
    act(() => result.current.url.setFilePath('threads/01_foo/README.md'))
    expect(result.current.pathname).toBe('/p/X/files/threads/01_foo/README.md')
    expect(result.current.url.section).toBe('files')
    expect(result.current.url.filePath).toBe('threads/01_foo/README.md')
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
  it('Back from /p/X/runs to /p/X updates the hook', async () => {
    // MemoryRouter accepts initialEntries as a stack; simulate two entries
    // and verify the hook sees the top.
    const { result } = renderHook(useUrlAndPath, {
      wrapper: ({ children }) => (
        <MemoryRouter initialEntries={['/p/X', '/p/X/runs']} initialIndex={1}>
          {children}
        </MemoryRouter>
      ),
    })
    expect(result.current.pathname).toBe('/p/X/runs')
    expect(result.current.url.section).toBe('runs')

    // Pop back via the browser-history API (which MemoryRouter implements).
    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X')
    expect(result.current.url.section).toBe('threads')
  })

  it('Back from /p/X/runs/e/Y to /p/X/runs clears focus', async () => {
    const { result } = renderHook(useUrlAndPath, {
      wrapper: ({ children }) => (
        <MemoryRouter initialEntries={['/p/X/runs', '/p/X/runs/e/Y']} initialIndex={1}>
          {children}
        </MemoryRouter>
      ),
    })
    expect(result.current.url.focusedId).toBe('Y')

    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X/runs')
    expect(result.current.url.focusedId).toBe('workspace')
    expect(result.current.url.section).toBe('runs')
  })

  it('sequential setSection calls produce expected history entries', async () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X') })
    act(() => result.current.url.setSection('runs'))
    act(() => result.current.url.setSection('claims'))
    expect(result.current.pathname).toBe('/p/X/claims')
    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X/runs')
    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X')
  })
})
