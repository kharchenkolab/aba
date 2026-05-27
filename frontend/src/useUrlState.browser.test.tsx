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
    // Reset window.location between tests so each starts at '/'.
    window.history.pushState(null, '', '/')
  })

  it('reflects initial pathname from window.location', () => {
    window.history.pushState(null, '', '/p/X/runs')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })
    expect(result.current.pathname).toBe('/p/X/runs')
    expect(result.current.url.section).toBe('runs')
  })

  it('setSection navigates and the hook re-renders', () => {
    window.history.pushState(null, '', '/p/X')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })
    expect(result.current.pathname).toBe('/p/X')
    expect(result.current.url.section).toBe('threads')

    act(() => result.current.url.setSection('runs'))
    expect(result.current.pathname).toBe('/p/X/runs')
    expect(result.current.url.section).toBe('runs')
    expect(window.location.pathname).toBe('/p/X/runs')

    act(() => result.current.url.setSection('claims'))
    expect(result.current.pathname).toBe('/p/X/claims')
    expect(window.location.pathname).toBe('/p/X/claims')
  })

  it('window.history.back() triggers a re-render via popstate', async () => {
    window.history.pushState(null, '', '/p/X')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })
    act(() => result.current.url.setSection('runs'))
    expect(result.current.pathname).toBe('/p/X/runs')

    await act(async () => {
      window.history.back()
      // Allow popstate to flush.
      await new Promise(r => setTimeout(r, 0))
    })
    expect(window.location.pathname).toBe('/p/X')
    expect(result.current.pathname).toBe('/p/X')
    expect(result.current.url.section).toBe('threads')
  })

  it('Back from /p/X/runs/e/Y clears focus + keeps section', async () => {
    window.history.pushState(null, '', '/p/X/runs')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })
    act(() => result.current.url.setFocus('Y'))
    expect(result.current.pathname).toBe('/p/X/runs/e/Y')
    expect(result.current.url.focusedId).toBe('Y')

    await act(async () => {
      window.history.back()
      await new Promise(r => setTimeout(r, 0))
    })
    expect(result.current.pathname).toBe('/p/X/runs')
    expect(result.current.url.section).toBe('runs')
    expect(result.current.url.focusedId).toBe('workspace')
  })

  it('multiple setSection calls each push to window.history', async () => {
    window.history.pushState(null, '', '/p/X')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })
    act(() => result.current.url.setSection('runs'))
    act(() => result.current.url.setSection('claims'))
    act(() => result.current.url.setSection('data'))

    await act(async () => { window.history.back(); await new Promise(r => setTimeout(r, 0)) })
    expect(result.current.pathname).toBe('/p/X/claims')
    await act(async () => { window.history.back(); await new Promise(r => setTimeout(r, 0)) })
    expect(result.current.pathname).toBe('/p/X/runs')
    await act(async () => { window.history.back(); await new Promise(r => setTimeout(r, 0)) })
    expect(result.current.pathname).toBe('/p/X')
  })
})
