/**
 * URL ↔ state round-trip tests (MemoryRouter).
 *
 * The hook keeps a small module-level "recent tab click" flag so that
 * a tab → item-click sequence collapses into one history entry. Tests
 * reset that flag between cases via _resetCoalesceForTesting.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { useUrlState, _resetCoalesceForTesting } from './useUrlState'

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

beforeEach(() => { _resetCoalesceForTesting() })

describe('useUrlState — parsing', () => {
  it('parses root as home', () => {
    const { result } = renderHook(() => useUrlState(), { wrapper: wrap('/') })
    expect(result.current.pid).toBe(null)
    expect(result.current.isHome).toBe(true)
    expect(result.current.section).toBe('threads')
    expect(result.current.threadId).toBe('default')
    expect(result.current.focusedId).toBe('workspace')
  })

  it('parses bare project URL — default section', () => {
    const { result } = renderHook(() => useUrlState(), { wrapper: wrap('/p/X') })
    expect(result.current.pid).toBe('X')
    expect(result.current.section).toBe('threads')
  })

  it('parses section explicitly', () => {
    const { result } = renderHook(() => useUrlState(), { wrapper: wrap('/p/X/runs') })
    expect(result.current.section).toBe('runs')
  })

  it('parses section + thread + entity', () => {
    const { result } = renderHook(() => useUrlState(), { wrapper: wrap('/p/X/claims/t/T/e/E') })
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

  it('parses /files/e/<eid> as section=files + entity (no file open)', () => {
    // Regression: previously this URL was misparsed as filePath='e/<eid>',
    // and FileCanvas tried to fetch that path → 404 ("Viewer lookup
    // failed"). It happens when the user clicks the Files tab while an
    // entity is focused.
    const { result } = renderHook(() => useUrlState(), { wrapper: wrap('/p/X/files/e/res_abc') })
    expect(result.current.section).toBe('files')
    expect(result.current.filePath).toBe('')
    expect(result.current.focusedId).toBe('res_abc')
  })

  it('parses /files/t/<tid> as section=files + thread (no file open)', () => {
    const { result } = renderHook(() => useUrlState(), { wrapper: wrap('/p/X/files/t/T/e/E') })
    expect(result.current.section).toBe('files')
    expect(result.current.filePath).toBe('')
    expect(result.current.threadId).toBe('T')
    expect(result.current.focusedId).toBe('E')
  })

  it('files path that LOOKS like a normal segment but isn\'t reserved still parses as filePath', () => {
    // "threads" is a section name (consumed as a section before /files/),
    // not a reserved nav word. After /files/, only t/e/overview/inventory
    // are reserved.
    const { result } = renderHook(() => useUrlState(), {
      wrapper: wrap('/p/X/files/threads/01_foo/README.md'),
    })
    expect(result.current.section).toBe('files')
    expect(result.current.filePath).toBe('threads/01_foo/README.md')
  })
})

describe('useUrlState — setters', () => {
  it('setFocus preserves section + thread', () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/runs/t/T') })
    act(() => result.current.url.setFocus('E'))
    expect(result.current.pathname).toBe('/p/X/runs/t/T/e/E')
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

  it('setFilePath opens a file', () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/claims') })
    act(() => result.current.url.setFilePath('threads/01_foo/README.md'))
    expect(result.current.pathname).toBe('/p/X/files/threads/01_foo/README.md')
  })

  it('setScene toggles inventory / overview / off', () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/t/T') })
    act(() => result.current.url.setScene('inventory'))
    expect(result.current.pathname).toBe('/p/X/t/T/inventory')
    act(() => result.current.url.setScene(null))
    expect(result.current.pathname).toBe('/p/X/t/T')
  })
})

describe('useUrlState — Back via MemoryRouter', () => {
  it('Back from /p/X/runs to /p/X', async () => {
    const { result } = renderHook(useUrlAndPath, {
      wrapper: ({ children }) => (
        <MemoryRouter initialEntries={['/p/X', '/p/X/runs']} initialIndex={1}>{children}</MemoryRouter>
      ),
    })
    expect(result.current.url.section).toBe('runs')
    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X')
    expect(result.current.url.section).toBe('threads')
  })

  it('Back from /p/X/runs/e/Y to /p/X/runs (no coalesce, direct push)', async () => {
    // History was pre-loaded — no setSection call, so no coalesce flag.
    const { result } = renderHook(useUrlAndPath, {
      wrapper: ({ children }) => (
        <MemoryRouter initialEntries={['/p/X/runs', '/p/X/runs/e/Y']} initialIndex={1}>{children}</MemoryRouter>
      ),
    })
    expect(result.current.url.focusedId).toBe('Y')
    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X/runs')
  })
})

describe('useUrlState — tab → item COALESCE (the user-facing fix)', () => {
  it('setSection then setFocus replaces the tab entry (Back skips the tab change)', async () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/t/T') })
    // User starts at /p/X/t/T (threads tab, thread T selected, no focus)
    expect(result.current.pathname).toBe('/p/X/t/T')

    // Clicks Runs tab — pushes /p/X/runs/t/T AND marks coalesce-eligible
    act(() => result.current.url.setSection('runs'))
    expect(result.current.pathname).toBe('/p/X/runs/t/T')

    // Clicks a run within ~1.5s — REPLACES /p/X/runs/t/T with the focus URL
    act(() => result.current.url.setFocus('R'))
    expect(result.current.pathname).toBe('/p/X/runs/t/T/e/R')

    // One Back should land back at /p/X/t/T (skipping /p/X/runs/t/T)
    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X/t/T')
    expect(result.current.url.section).toBe('threads')
    expect(result.current.url.focusedId).toBe('workspace')
  })

  it('setFocus without preceding setSection still pushes normally', async () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/runs/t/T') })
    act(() => result.current.url.setFocus('R'))
    expect(result.current.pathname).toBe('/p/X/runs/t/T/e/R')

    // One Back should land at /p/X/runs/t/T (the entry we were on)
    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X/runs/t/T')
  })

  it('other navigations between setSection and setFocus clear the coalesce flag', async () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X/t/T') })
    act(() => result.current.url.setSection('runs'))           // marks coalesce
    act(() => result.current.url.setThread('T2'))              // clears coalesce
    act(() => result.current.url.setFocus('R'))                // normal push, no replace
    expect(result.current.pathname).toBe('/p/X/runs/t/T2/e/R')

    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X/runs/t/T2')     // focus undone, NOT thread change
  })

  it('two consecutive tab clicks each push (only the last one is coalesce-eligible)', async () => {
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap('/p/X') })
    act(() => result.current.url.setSection('runs'))           // marks coalesce
    act(() => result.current.url.setSection('claims'))         // re-marks coalesce
    act(() => result.current.url.setFocus('C'))                // replaces /p/X/claims with /p/X/claims/e/C

    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X/runs')          // the previous tab push remains
    await act(async () => { result.current.back() })
    expect(result.current.pathname).toBe('/p/X')
  })
})
