/**
 * App-render smoke test.
 *
 * Loads the WHOLE App module graph and mounts <App /> inside a
 * BrowserRouter (the same shell main.tsx uses), then asserts the
 * #root container has rendered something.
 *
 * What this catches that the per-component tests miss: stale import
 * paths after a directory reorg, broken default exports, top-level
 * throws in side-effect modules (registry registration, etc.). The
 * dev server transforms modules lazily and individual vitest test
 * files only pull what they need, so a fatal import in App's
 * transitive graph survives until something actually navigates to
 * the affected screen — i.e., a user opening the front page.
 *
 * Keep this test cheap: a single mount, no interaction. Failure mode
 * is import/render exception, surfaced by the test runner with a
 * stack trace pointing at the offending file.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import App from './App'

beforeEach(() => {
  // App boot fires off /api/projects + /api/context-suggestions + a few
  // similar housekeeping fetches. Stub them with safe empties so the
  // mount finishes without unhandled-rejection noise. This test cares
  // about MODULE LOAD + RENDER, not data flows.
  globalThis.fetch = vi.fn(async () => new Response('[]', {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })) as unknown as typeof fetch
  // happy-dom doesn't ship EventSource; App opens one for /api/notifications.
  class _NoopES { close() {} onmessage = null as ((e: MessageEvent) => void) | null; onerror = null }
  ;(globalThis as unknown as { EventSource: typeof _NoopES }).EventSource = _NoopES
})

describe('App smoke', () => {
  it('mounts without throwing', () => {
    const { container } = render(<MemoryRouter><App /></MemoryRouter>)
    // The mount itself is the assertion. If a module-load error happened
    // the call above would have thrown. We just sanity-check there's
    // SOME DOM under #root so we'd notice if React quietly punted.
    expect(container.innerHTML.length).toBeGreaterThan(0)
  })
})
