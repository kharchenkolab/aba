/**
 * Settings → Modules tab (misc/modules.md). Capability packs beyond the platform
 * core: the Python analysis stack, the R toolchain, viewers. Each can be enabled
 * (background install) or disabled; disabling a removable module keeps it on disk with
 * a "reclaim space" affordance. Live state polled from /api/modules while anything is
 * installing. This is also where staged-install completion ("Setting up…") surfaces.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

type Mode = 'on' | 'first_use' | 'off'
interface Module {
  id: string; title: string; description: string
  size: string; est_time: string
  default_state: Mode; removable: boolean; first_use: string[]
  mode: Mode; enabled: boolean
  actual: 'ready' | 'installing' | 'queued' | 'failed' | 'not_installed'
  on_disk: boolean; progress: string | null; error: string | null; version: string | null
}

const MODE_LABELS: { key: Mode; label: string }[] = [
  { key: 'on', label: 'On' },
  { key: 'first_use', label: 'First use' },
  { key: 'off', label: 'Off' },
]

function chip(m: Module): { text: string; cls: string } {
  switch (m.actual) {
    case 'ready': return { text: '✓ Ready', cls: 'is-ok' }
    case 'installing': return { text: '⏳ Installing…', cls: 'is-busy' }
    case 'queued': return { text: '• Queued', cls: 'is-busy' }
    case 'failed': return { text: '✗ Failed', cls: 'is-bad' }
    default: return m.mode === 'on'
      ? { text: 'Pending', cls: 'is-busy' }
      : m.mode === 'first_use'
        ? { text: 'Installs on first use', cls: 'is-off' }
        : { text: 'Off', cls: 'is-off' }
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
  const setMode = (id: string, mode: Mode) => act(id, `mode?mode=${mode}`)

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
                <span className="mod-card__title">{m.title}</span>
                <span className="mod-card__meta">{m.size} · {m.est_time}</span>
                <span className={`mod-chip ${c.cls}`}>{c.text}</span>
              </div>
              <p className="mod-card__desc">{m.description}</p>
              <div className="mod-seg" role="group" aria-label={`${m.title} mode`}>
                {MODE_LABELS.map(o => (
                  <button key={o.key}
                    className={`mod-seg__btn ${m.mode === o.key ? 'is-active' : ''}`}
                    aria-pressed={m.mode === o.key}
                    disabled={busy === m.id || m.mode === o.key}
                    onClick={() => setMode(m.id, o.key)}>{o.label}</button>
                ))}
              </div>
              {installing && m.progress && <div className="mod-card__progress">{m.progress}</div>}
              {m.actual === 'failed' && (
                <div className="mod-card__error">
                  {m.error || 'Install failed.'}{' '}
                  <button className="mod-linkbtn" disabled={busy === m.id}
                    onClick={() => act(m.id, 'retry')}>Retry</button>
                </div>
              )}
              {m.mode === 'off' && m.on_disk && m.removable && (
                <button className="mod-linkbtn mod-reclaim" disabled={busy === m.id}
                  onClick={() => act(m.id, 'mode?mode=off&remove=true')}>Reclaim disk space</button>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
