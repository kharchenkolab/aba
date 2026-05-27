/**
 * BrowserRouter version of the routing tests — same code path as production.
 * happy-dom implements window.history APIs so the popstate subscription works.
 */
import { beforeEach, describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { BrowserRouter, useLocation, useNavigate } from 'react-router-dom'
import { useUrlState, _resetCoalesceForTesting } from './useUrlState'

function wrap({ children }: { children: React.ReactNode }) {
  return <BrowserRouter>{children}</BrowserRouter>
}

function useUrlAndPath() {
  const url = useUrlState()
  const location = useLocation()
  const navigate = useNavigate()
  return { url, pathname: location.pathname, back: () => navigate(-1) }
}

beforeEach(() => {
  window.history.pushState(null, '', '/')
  _resetCoalesceForTesting()
})

describe('useUrlState under BrowserRouter', () => {
  it('reflects initial pathname from window.location', () => {
    window.history.pushState(null, '', '/p/X/runs/e/Y')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })
    expect(result.current.url.section).toBe('runs')
    expect(result.current.url.focusedId).toBe('Y')
  })

  it('setSection then setFocus replaces — Back skips the tab change', async () => {
    window.history.pushState(null, '', '/p/X/t/T')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })

    act(() => result.current.url.setSection('runs'))
    expect(window.location.pathname).toBe('/p/X/runs/t/T')

    act(() => result.current.url.setFocus('R'))
    expect(window.location.pathname).toBe('/p/X/runs/t/T/e/R')

    await act(async () => { window.history.back(); await new Promise(r => setTimeout(r, 0)) })
    expect(window.location.pathname).toBe('/p/X/t/T')
  })

  it('setFocus without preceding setSection pushes normally', async () => {
    window.history.pushState(null, '', '/p/X/runs/t/T')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })
    act(() => result.current.url.setFocus('R'))
    expect(window.location.pathname).toBe('/p/X/runs/t/T/e/R')

    await act(async () => { window.history.back(); await new Promise(r => setTimeout(r, 0)) })
    expect(window.location.pathname).toBe('/p/X/runs/t/T')
  })

  it('Back from /p/X/files/foo.csv unwinds the file', async () => {
    window.history.pushState(null, '', '/p/X')
    const { result } = renderHook(useUrlAndPath, { wrapper: wrap })
    act(() => result.current.url.setFilePath('foo.csv'))
    expect(window.location.pathname).toBe('/p/X/files/foo.csv')

    await act(async () => { window.history.back(); await new Promise(r => setTimeout(r, 0)) })
    expect(window.location.pathname).toBe('/p/X')
  })
})
