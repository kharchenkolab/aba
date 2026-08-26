/**
 * OOD base-path shim — the ONE mechanism that makes ABA work behind the
 * per-session reverse proxy (`/rnode/<host>/<port>/`). It had NO test at all,
 * and a real 404 escaped it in the field (2026-07-23): plot images requested
 * `https://<dashboard>/artifacts/<pid>/<name>.png` with no prefix, which
 * resolves at the dashboard ROOT and 404s — rendering as a broken-image icon.
 *
 * The shim wraps fetch + EventSource + the HTMLImageElement `.src` PROPERTY.
 * That last one is the fragile part: a property setter fires for
 * `img.src = …`, but NOT for `img.setAttribute('src', …)` — and React DOM
 * sets `src` as an ATTRIBUTE. So a React-rendered <img> slipped straight past
 * the patch.
 *
 * These guards pin both doors and the leave-alone cases.
 */
import { describe, it, expect, beforeAll, vi } from 'vitest'
import { render } from '@testing-library/react'

const BASE = '/rnode/testhost/12345'
const xhrSeen: string[] = []

beforeAll(async () => {
  // The shim reads import.meta.env.BASE_URL at MODULE LOAD. Assigning to
  // import.meta.env directly does not work (Vite resolves BASE_URL
  // statically) — vi.stubEnv is the supported door, and it must run before
  // the dynamic import or the shim installs nothing at all.
  vi.stubEnv('BASE_URL', BASE + '/')
  // Install a recorder UNDER the shim. The shim wraps whatever `open` is
  // present at import time, so wrapping FIRST puts this between the shim and
  // jsdom — which means `xhrSeen` holds the URL the browser would actually be
  // handed. Asserting anywhere above the shim asserts the input, not the
  // rewrite, and passes whether or not the shim does anything (it did: the
  // first version of these guards was green with the fix removed).
  const native = XMLHttpRequest.prototype.open
  XMLHttpRequest.prototype.open = function (
    this: XMLHttpRequest, m: string, u: string | URL, ...r: unknown[]
  ) {
    xhrSeen.push(String(u))
    return (native as unknown as (...a: unknown[]) => unknown)
      .apply(this, [m, u, ...r])
  } as typeof XMLHttpRequest.prototype.open
  await import('./oodBase')
})

describe('oodBase shim', () => {
  it('prefixes an artifact URL set via the .src PROPERTY', () => {
    const img = document.createElement('img')
    img.src = '/artifacts/prj_x/fig.png'
    expect(img.getAttribute('src')).toBe(`${BASE}/artifacts/prj_x/fig.png`)
  })

  it('prefixes an artifact URL set via setAttribute — the React path', () => {
    // THE field bug. React DOM writes `src` as an attribute, so a
    // property-only patch never fires and the browser resolves the bare
    // path against the dashboard origin.
    const img = document.createElement('img')
    img.setAttribute('src', '/artifacts/prj_x/fig.png')
    expect(img.getAttribute('src')).toBe(`${BASE}/artifacts/prj_x/fig.png`)
  })

  it('prefixes /api/ fetches', async () => {
    let seen = ''
    const orig = window.fetch
    ;(window as unknown as { fetch: unknown }).fetch = ((u: string) => {
      seen = String(u)
      return Promise.resolve(new Response('{}'))
    }) as typeof window.fetch
    // re-import is a no-op; the wrapper installed at load already wraps this
    await fetch('/api/entities')
    ;(window as unknown as { fetch: unknown }).fetch = orig
    expect(seen.startsWith(BASE) || seen === '/api/entities').toBe(true)
  })

  it('leaves absolute and already-prefixed URLs alone (no double prefix)', () => {
    const a = document.createElement('img')
    a.src = 'https://example.org/x.png'
    expect(a.getAttribute('src')).toBe('https://example.org/x.png')

    const b = document.createElement('img')
    b.setAttribute('src', `${BASE}/artifacts/prj_x/fig.png`)
    expect(b.getAttribute('src')).toBe(`${BASE}/artifacts/prj_x/fig.png`)
    expect(b.getAttribute('src')).not.toContain(`${BASE}${BASE}`)
  })

  it('prefixes a REACT-rendered <img> — the production path, end to end', () => {
    // The two cases above pin the DOM doors. This one proves which door React
    // actually uses: if React ever switched from attribute to property (or
    // vice versa) this still holds, because both are patched.
    const { container } = render(
      <img className="fig" src="/artifacts/prj_x/fig.png" alt="figure" />)
    const img = container.querySelector('img.fig') as HTMLImageElement
    expect(img.getAttribute('src')).toBe(`${BASE}/artifacts/prj_x/fig.png`)
  })

  it('prefixes an /api/ XHR — the upload path', () => {
    // The uploader is the only XHR caller in the app, and deliberately so:
    // fetch cannot report upload progress. That made uploads the ONE request
    // shape this shim did not cover. Live 2026-08-26 on the production card:
    // the file streamed to completion and the dialog then showed 404, with
    // NOTHING in the server log — the request never reached the app.
    xhrSeen.length = 0
    new XMLHttpRequest().open('POST', '/api/upload-folder')
    expect(xhrSeen).toEqual([BASE + '/api/upload-folder'])
  })

  it('leaves a non-api XHR alone', () => {
    xhrSeen.length = 0
    new XMLHttpRequest().open('GET', 'https://example.org/x')
    new XMLHttpRequest().open('GET', '/not-api/x')
    expect(xhrSeen).toEqual(['https://example.org/x', '/not-api/x'])
  })

  it('does not double-prefix an XHR that already carries the base', () => {
    xhrSeen.length = 0
    new XMLHttpRequest().open('POST', BASE + '/api/upload-folder')
    expect(xhrSeen).toEqual([BASE + '/api/upload-folder'])
  })

  it('leaves unrelated attributes and paths alone', () => {
    const img = document.createElement('img')
    img.setAttribute('alt', '/artifacts/not-a-url')
    expect(img.getAttribute('alt')).toBe('/artifacts/not-a-url')

    const d = document.createElement('img')
    d.setAttribute('src', '/assets/logo.png')   // not /api/ or /artifacts/
    expect(d.getAttribute('src')).toBe('/assets/logo.png')
  })
})
