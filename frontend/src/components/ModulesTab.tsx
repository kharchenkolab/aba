/**
 * Settings → Modules tab (misc/modules.md). Capability packs beyond the platform
 * core: the Python analysis stack, the R toolchain, viewers. Each can be enabled
 * (background install) or disabled; disabling a removable module keeps it on disk with
 * a "reclaim space" affordance. Live state polled from /api/modules while anything is
 * installing. This is also where staged-install completion ("Setting up…") surfaces.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

interface Module {
  id: string; title: string; description: string
  size: string; est_time: string
  default_enabled: boolean; removable: boolean; first_use: string[]
  enabled: boolean
  actual: 'ready' | 'installing' | 'queued' | 'failed' | 'not_installed'
  on_disk: boolean; progress: string | null; error: string | null; version: string | null
}

function chip(m: Module): { text: string; cls: string } {
  switch (m.actual) {
    case 'ready': return { text: '✓ Ready', cls: 'is-ok' }
    case 'installing': return { text: '⏳ Installing…', cls: 'is-busy' }
    case 'queued': return { text: '• Queued', cls: 'is-busy' }
    case 'failed': return { text: '✗ Failed', cls: 'is-bad' }
    default: return m.enabled
      ? { text: 'Pending', cls: 'is-busy' }
      : { text: 'Not installed', cls: 'is-off' }
  }
}

export default function ModulesTab() {
  const [mods, setMods] = useState<Module[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)   // module id with an in-flight action
  const stop = useRef(false)

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/modules')
      if (r.ok && !stop.current) setMods((await r.json()).modules)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    stop.current = false
    load()
    return () => { stop.current = true }
  }, [load])

  // Live-refresh while anything is installing/queued.
  useEffect(() => {
    if (!mods?.some(m => m.actual === 'installing' || m.actual === 'queued')) return
    const t = setTimeout(load, 4000)
    return () => clearTimeout(t)
  }, [mods, load])

  async function act(id: string, path: string) {
    setBusy(id)
    try {
      const r = await fetch(`/api/modules/${encodeURIComponent(id)}/${path}`, { method: 'POST' })
      if (r.ok) { const v: Module = await r.json(); setMods(ms => ms?.map(m => m.id === id ? v : m) ?? ms) }
    } catch { /* ignore */ } finally { setBusy(null); load() }
  }

  if (!mods) return <div className="settings__empty">Loading…</div>

  return (
    <section className="settings__section">
      <h3 className="settings__section-title">Modules</h3>
      <p className="settings__hint">
        Analysis capabilities beyond the core app. Enabled modules install in the
        background; the app stays usable meanwhile.
      </p>
      <ul className="mod-list">
        {mods.map(m => {
          const c = chip(m)
          const installing = m.actual === 'installing' || m.actual === 'queued'
          return (
            <li key={m.id} className="mod-card">
              <div className="mod-card__head">
                <label className="mod-toggle">
                  <input type="checkbox" checked={m.enabled} disabled={busy === m.id || installing}
                    onChange={() => act(m.id, m.enabled ? 'disable' : 'enable')} />
                  <span className="mod-card__title">{m.title}</span>
                </label>
                <span className={`mod-chip ${c.cls}`}>{c.text}</span>
              </div>
              <p className="mod-card__desc">{m.description}</p>
              <div className="mod-card__meta">
                <span>{m.size}</span><span>·</span><span>{m.est_time}</span>
                {!m.enabled && m.first_use.length > 0 && <span>· installs on first use</span>}
              </div>
              {installing && m.progress && <div className="mod-card__progress">{m.progress}</div>}
              {m.actual === 'failed' && (
                <div className="mod-card__error">
                  {m.error || 'Install failed.'}{' '}
                  <button className="mod-linkbtn" disabled={busy === m.id}
                    onClick={() => act(m.id, 'retry')}>Retry</button>
                </div>
              )}
              {!m.enabled && m.on_disk && m.removable && (
                <button className="mod-linkbtn mod-reclaim" disabled={busy === m.id}
                  onClick={() => act(m.id, 'disable?remove=true')}>Reclaim disk space</button>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
