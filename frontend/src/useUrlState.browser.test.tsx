/**
 * BrowserRouter version of the routing tests. happy-dom implements
 * window.history.{push,replace,back,forward}State and the popstate event,
 * so React Router's BrowserRouter subscription works here — this is the
 * same code path the real browser uses.
 *
 * If these pass but the user still sees Back not working, the bug is
 * somewhere outside useUrlState (e.g. in App's effect/render wiring).
 */
import { beforeEach, describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { BrowserRouter, useLocation, useNavigate } from 'react-router-dom'
import { useUrlState } from './useUrlState'

function wrap({ children }: { children: React.ReactNode }) {
  return <BrowserRouter>{children}</BrowserRouter>
}

function useUrlAndPath() {
  const url = useUrlState()
  const location = useLocation()
  const navigate = useNavigate()
  return { url, pathname: location.pathname, back: () => navigate(-1) }
}

describe('useUrlState under BrowserRouter (production code path)', () => {
  beforeEach(() => {
    window.history.pushState(null, '', '/')
  })

  it('reflects initial pathname from window.location', () => {
    window.history.pushState(null, '', '/p/X/t/T/e/E')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })
    expect(result.current.pathname).toBe('/p/X/t/T/e/E')
    expect(result.current.url.threadId).toBe('T')
    expect(result.current.url.focusedId).toBe('E')
  })

  it('setFocus navigates and the hook re-renders', () => {
    window.history.pushState(null, '', '/p/X/t/T')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })
    act(() => result.current.url.setFocus('E'))
    expect(result.current.pathname).toBe('/p/X/t/T/e/E')
    expect(window.location.pathname).toBe('/p/X/t/T/e/E')
  })

  it('window.history.back() triggers a re-render via popstate', async () => {
    window.history.pushState(null, '', '/p/X')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })
    act(() => result.current.url.setThread('T'))
    expect(result.current.pathname).toBe('/p/X/t/T')

    await act(async () => {
      window.history.back()
      await new Promise(r => setTimeout(r, 0))
    })
    expect(window.location.pathname).toBe('/p/X')
    expect(result.current.pathname).toBe('/p/X')
    expect(result.current.url.threadId).toBe('default')
  })

  it('Back from /p/X/t/T/e/Y clears focus, keeps thread', async () => {
    window.history.pushState(null, '', '/p/X/t/T')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })
    act(() => result.current.url.setFocus('Y'))
    expect(result.current.pathname).toBe('/p/X/t/T/e/Y')

    await act(async () => {
      window.history.back()
      await new Promise(r => setTimeout(r, 0))
    })
    expect(result.current.pathname).toBe('/p/X/t/T')
    expect(result.current.url.threadId).toBe('T')
    expect(result.current.url.focusedId).toBe('workspace')
  })

  it('Back from /p/X/files/foo.csv unwinds the file', async () => {
    window.history.pushState(null, '', '/p/X')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })
    act(() => result.current.url.setFilePath('foo.csv'))
    expect(result.current.pathname).toBe('/p/X/files/foo.csv')

    await act(async () => {
      window.history.back()
      await new Promise(r => setTimeout(r, 0))
    })
    expect(result.current.pathname).toBe('/p/X')
    expect(result.current.url.filePath).toBe('')
  })
})
