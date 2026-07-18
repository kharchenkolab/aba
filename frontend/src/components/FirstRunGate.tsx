import { useEffect, useRef, useState } from 'react'
import { apiGet } from '../lib/api'

const SKIP_KEY = 'aba:skip-provider-setup'

// localStorage can be absent (node's experimental global without a backing
// file — the mount smoke test) or throw on access (storage-blocked browser
// modes); the gate must degrade to "not skipped", never crash the App mount.
function skipRemembered(): boolean {
  try { return globalThis.localStorage?.getItem(SKIP_KEY) === '1' } catch { return false }
}
function rememberSkip(): void {
  try { globalThis.localStorage?.setItem(SKIP_KEY, '1') } catch { /* storage blocked */ }
}

/**
 * First-run gate (lazy_env_init.md Phase C): the backend serves CREDENTIAL-LESS —
 * data management, file browsing, and viewers work without a model. When no provider
 * is connected we invite the user to connect one (chat needs it) via Settings → Agent,
 * or to skip for now. Non-blocking banner; dismissal (Skip) is remembered (localStorage).
 *
 * The banner reflects REAL state: opening Settings does NOT dismiss it — we re-derive
 * from /api/settings/credential/any when Settings closes, so cancelling out (without
 * connecting) keeps the banner up instead of looking as if it were solved.
 */
export default function FirstRunGate(
  { settingsOpen, onOpenSettings }: { settingsOpen: boolean; onOpenSettings: () => void },
) {
  const [show, setShow] = useState(false)
  const wasOpen = useRef(false)

  function recheck() {
    if (skipRemembered()) { setShow(false); return }
    apiGet<{ configured: boolean }>('/api/settings/credential/any')
      .then(d => setShow(d.configured === false))
      .catch(() => { /* backend not ready / offline — leave as-is */ })
  }

  useEffect(() => { recheck() }, [])
  // Re-derive whenever Settings transitions open → closed (a connect there flips
  // credential/any to configured; a cancel leaves it unconfigured → banner stays).
  useEffect(() => {
    if (wasOpen.current && !settingsOpen) recheck()
    wasOpen.current = settingsOpen
  }, [settingsOpen])

  // Hide while Settings is open so the bar doesn't overlap the modal; state is
  // re-derived on close.
  if (!show || settingsOpen) return null
  return (
    <div
      role="status"
      style={{
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 1000,
        display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'center',
        flexWrap: 'wrap', padding: '8px 16px', background: '#1f2937', color: '#f9fafb',
        fontSize: 14, boxShadow: '0 1px 4px rgba(0,0,0,.25)',
      }}
    >
      <span>
        Connect a model provider to chat with Guide — data management, files, and viewers
        work without one.
      </span>
      <span style={{ display: 'flex', gap: 8 }}>
        <button
          onClick={() => onOpenSettings()}
          style={{
            padding: '4px 12px', borderRadius: 6, border: 'none', cursor: 'pointer',
            background: '#3b82f6', color: '#fff', fontWeight: 600,
          }}
        >
          Connect a provider
        </button>
        <button
          onClick={() => { rememberSkip(); setShow(false) }}
          style={{
            padding: '4px 12px', borderRadius: 6, cursor: 'pointer',
            background: 'transparent', color: '#d1d5db', border: '1px solid #4b5563',
          }}
        >
          Skip for now
        </button>
      </span>
    </div>
  )
}
