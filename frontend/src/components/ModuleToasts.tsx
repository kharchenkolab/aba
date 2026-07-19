/**
 * Module install toasts (misc/modules.md). Listens for `aba:module` window events
 * (re-dispatched from the /api/notifications SSE by App) and shows live install
 * progress: queued/installing → a spinner; ready → a brief ✓; failed → a sticky card
 * with Retry and "Ask Guide" (routes the error into the chat composer via the existing
 * aba:viewer-error seam, so a click-triggered failure becomes an agent turn).
 */
import { useEffect, useState } from 'react'
import { apiPost } from '../lib/api'

type MState = 'queued' | 'installing' | 'ready' | 'failed'
interface Ev { id: string; title: string; state: MState; progress?: string | null; error?: string | null }

export default function ModuleToasts() {
  const [items, setItems] = useState<Ev[]>([])

  useEffect(() => {
    const onEv = (e: Event) => {
      const d = (e as CustomEvent).detail as Ev
      if (!d || !d.id) return
      setItems(prev => {
        const next = prev.filter(p => p.id !== d.id)
        next.push(d)
        return next
      })
      if (d.state === 'ready') {
        setTimeout(() => setItems(prev => prev.filter(p => p.id !== d.id)), 4000)
      }
    }
    window.addEventListener('aba:module', onEv as EventListener)
    return () => window.removeEventListener('aba:module', onEv as EventListener)
  }, [])

  const dismiss = (id: string) => setItems(prev => prev.filter(p => p.id !== id))
  const retry = (id: string) => {
    apiPost(`/api/modules/${encodeURIComponent(id)}/retry`).catch(() => {})
  }
  const askGuide = (it: Ev) => {
    // Reuse the viewer-error → bug-composer seam (App listens for this).
    window.postMessage({ type: 'aba:viewer-error',
      context: { viewer: `module:${it.id}`, file: it.title, error: it.error || 'install failed' } },
      location.origin)
    dismiss(it.id)
  }

  if (!items.length) return null
  return (
    <div className="mtoast-host">
      {items.map(it => (
        <div key={it.id} className={`mtoast mtoast--${it.state}`} role="status">
          <div className="mtoast__row">
            <span className="mtoast__title">{it.title}</span>
            <button className="mtoast__x" onClick={() => dismiss(it.id)} aria-label="Dismiss">×</button>
          </div>
          {it.state === 'ready' ? (
            <div className="mtoast__body mtoast__ok">✓ Ready</div>
          ) : it.state === 'failed' ? (
            <>
              <div className="mtoast__body mtoast__err">Install failed{it.error ? `: ${it.error}` : ''}.</div>
              <div className="mtoast__actions">
                <button onClick={() => retry(it.id)}>Retry</button>
                <button onClick={() => askGuide(it)}>Ask Guide</button>
              </div>
            </>
          ) : (
            <div className="mtoast__body">{it.progress || 'Installing…'}</div>
          )}
        </div>
      ))}
    </div>
  )
}
