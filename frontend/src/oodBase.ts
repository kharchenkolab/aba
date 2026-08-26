// OOD reverse-proxy base-path shim.
//
// When ABA is launched as an Open OnDemand interactive app it is served under
// a per-session prefix (e.g. /rnode/<host>/<port>/). The Vite build bakes that
// prefix into asset URLs + import.meta.env.BASE_URL, but the app also makes
// many absolute API calls (`/api/...`, `/artifacts/...`). Rather than rewrite
// every call site, we wrap fetch + XHR + EventSource here to prepend BASE_URL
// those absolute paths. In a normal install BASE_URL is "/" so this is a no-op.
//
// Imported FIRST in main.tsx so the wrappers are installed before any app code
// issues a request.
const BASE = (import.meta.env.BASE_URL || '/').replace(/\/$/, '')  // "" or "/rnode/h/p"

function withBase(u: string): string {
  if (BASE && (u.startsWith('/api/') || u.startsWith('/artifacts/'))) return BASE + u
  return u
}

if (BASE) {
  const _fetch = window.fetch.bind(window)
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    if (typeof input === 'string') return _fetch(withBase(input), init)
    if (input instanceof URL) return _fetch(withBase(input.pathname + input.search), init)
    if (input instanceof Request && input.url) {
      try {
        const p = new URL(input.url, window.location.origin)
        const np = withBase(p.pathname)
        if (np !== p.pathname) return _fetch(new Request(np + p.search, input), init)
      } catch { /* fall through */ }
    }
    return _fetch(input, init)
  }) as typeof window.fetch

  const _ES = window.EventSource
  if (_ES) {
    const Wrapped = function (url: string | URL, cfg?: EventSourceInit) {
      return new _ES(typeof url === 'string' ? withBase(url) : url, cfg)
    } as unknown as typeof EventSource
    Wrapped.prototype = _ES.prototype
    ;(Wrapped as { CONNECTING: number }).CONNECTING = _ES.CONNECTING
    ;(Wrapped as { OPEN: number }).OPEN = _ES.OPEN
    ;(Wrapped as { CLOSED: number }).CLOSED = _ES.CLOSED
    window.EventSource = Wrapped
  }

  // XHR. The uploader is the only XHR caller in the app, and deliberately so —
  // fetch cannot report upload progress, so the file dialog uses
  // XMLHttpRequest to hook `upload.onprogress`. That made uploads the ONE
  // request shape this shim did not cover: under OOD they posted to a bare
  // `/api/upload-folder`, the proxy had no route for it, and the browser
  // streamed the whole file before being answered 404 — with nothing in the
  // server log, because the request never reached the app. Observed live
  // 2026-08-26 on the production card.
  const _open = XMLHttpRequest.prototype.open
  XMLHttpRequest.prototype.open = function (
    this: XMLHttpRequest, method: string, url: string | URL, ...rest: unknown[]
  ) {
    const u = typeof url === 'string' ? withBase(url) : url
    // eslint-disable-next-line prefer-spread
    return (_open as unknown as (...a: unknown[]) => unknown).apply(
      this, [method, u, ...rest])
  } as typeof XMLHttpRequest.prototype.open

  // Plots/figures render via <img src="/artifacts/...">, set by React through
  // the .src DOM property (not fetch). Patch the setter so those absolute paths
  // get the BASE prefix too — otherwise they 404 under the OOD proxy.
  try {
    const d = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src')
    if (d && d.set) {
      const set = d.set
      Object.defineProperty(HTMLImageElement.prototype, 'src', {
        configurable: true,
        enumerable: d.enumerable,
        get: d.get,
        set(v: string) { set.call(this, typeof v === 'string' ? withBase(v) : v) },
      })
    }
    // …and the ATTRIBUTE door. The setter above fires for `img.src = x`, but
    // React DOM writes `src` with setAttribute, so a React-rendered <img>
    // bypassed the patch entirely and requested `/artifacts/...` at the
    // dashboard ROOT — a 404 that renders as a broken-image icon (live
    // 2026-07-23: "half the harvested plot icons showed as broken links").
    // Scoped to HTMLImageElement (an own property shadowing Element's), so
    // no other element's setAttribute is touched. withBase is idempotent —
    // an already-prefixed URL no longer starts with /artifacts/ or /api/.
    const setAttr = HTMLImageElement.prototype.setAttribute
    Object.defineProperty(HTMLImageElement.prototype, 'setAttribute', {
      configurable: true, writable: true,
      value(this: HTMLImageElement, name: string, value: string) {
        return setAttr.call(this, name,
          name === 'src' && typeof value === 'string' ? withBase(value) : value)
      },
    })
  } catch { /* non-DOM env */ }
}

// For non-fetch consumers (e.g. window.open of a launcher URL, which the fetch
// shim above doesn't cover): prepend the OOD base to any root-relative path.
export function withBasePath(u: string): string {
  return BASE && u.startsWith('/') ? BASE + u : u
}

export {}
