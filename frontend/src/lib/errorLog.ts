/**
 * Passive client-side error recorder for bug reports (misc/feedback.md B4).
 *
 * Guide can't see the browser, so UI-only failures are invisible to it. This
 * registers `error` + `unhandledrejection` listeners ONCE and keeps the last N
 * in a ring buffer. Cost is ~zero at rest: these are event-driven (no timers, no
 * polling, no network), and nothing is sent anywhere until the user files a
 * report (the bug button POSTs a snapshot, which Guide can then read + summarize).
 */
export interface ErrEntry {
  ts: number
  kind: 'error' | 'unhandledrejection'
  message: string
  source?: string
}

const CAP = 30
const buf: ErrEntry[] = []

function record(kind: ErrEntry['kind'], message: string, source?: string): void {
  buf.push({ ts: Date.now(), kind, message: String(message ?? '').slice(0, 500), source })
  while (buf.length > CAP) buf.shift()
}

let installed = false

/** Register the listeners once. `target` is injectable for tests. */
export function installErrorRecorder(target: EventTarget = window): void {
  if (installed) return
  installed = true
  target.addEventListener('error', (e: Event) => {
    const ee = e as ErrorEvent
    const src = ee.filename ? `${ee.filename}:${ee.lineno ?? 0}:${ee.colno ?? 0}` : undefined
    record('error', ee.message || String((ee as unknown as { error?: unknown }).error ?? 'error'), src)
  })
  target.addEventListener('unhandledrejection', (e: Event) => {
    const r = (e as PromiseRejectionEvent).reason as { message?: string } | undefined
    record('unhandledrejection', (r && (r.message || String(r))) || 'unhandled rejection')
  })
}

export function recentErrors(): ErrEntry[] {
  return buf.slice()
}

/** Compact, capped lines for a bug report's client_context. */
export function recentErrorLines(): string[] {
  return buf.slice(-CAP).map(e => `[${e.kind}] ${e.message}${e.source ? ' @ ' + e.source : ''}`)
}

/** test-only: clear buffer + allow re-install on a fresh target. */
export function __resetForTest(): void {
  buf.length = 0
  installed = false
}
