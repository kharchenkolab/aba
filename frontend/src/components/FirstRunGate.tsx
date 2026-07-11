import { useEffect, useState } from 'react'

const SKIP_KEY = 'aba:skip-provider-setup'

/**
 * First-run gate (lazy_env_init.md Phase C): the backend serves CREDENTIAL-LESS —
 * data management, file browsing, and viewers work without a model. When no provider
 * is connected we invite the user to connect one (chat needs it) via Settings → Agent,
 * or to skip for now. Non-blocking banner; dismissal is remembered (localStorage).
 */
export default function FirstRunGate({ onOpenSettings }: { onOpenSettings: () => void }) {
  const [show, setShow] = useState(false)

  useEffect(() => {
    if (localStorage.getItem(SKIP_KEY) === '1') return
    let cancelled = false
    fetch('/api/settings/credential/any')
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (!cancelled && d && d.configured === false) setShow(true) })
      .catch(() => { /* backend not ready / offline — stay hidden */ })
    return () => { cancelled = true }
  }, [])

  if (!show) return null
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
          onClick={() => { setShow(false); onOpenSettings() }}
          style={{
            padding: '4px 12px', borderRadius: 6, border: 'none', cursor: 'pointer',
            background: '#3b82f6', color: '#fff', fontWeight: 600,
          }}
        >
          Connect a provider
        </button>
        <button
          onClick={() => { localStorage.setItem(SKIP_KEY, '1'); setShow(false) }}
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
