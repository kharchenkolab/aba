/**
 * Drawer — right-rail panel that renders the structured Manifest the
 * backend assembles for the model each turn (T2.4 of the
 * arch3_plan.md follow-ups).
 *
 * The model only ever sees the rendered system string; this view is for
 * the scientist: which entity is focused, what was loaded for it, what
 * the thread carries, what policy applies. Read-only.
 *
 * Live: useChat exposes the most recent manifest from the SSE stream.
 * On initial mount (or thread switch with no chat yet), we fetch the
 * latest persisted snapshot from /api/threads/{tid}/manifest.
 */
import { useEffect, useState } from 'react'
import type { ManifestSnapshot } from '../types'
import './Drawer.css'

interface Props {
  manifest: ManifestSnapshot | null
  focusEntityId: string
  threadId: string | null
  onClose?: () => void
}

export default function Drawer({ manifest: liveManifest, focusEntityId, threadId, onClose }: Props) {
  // Live preview: whenever focus/thread changes, re-fetch what the agent
  // WOULD see if a turn ran right now. The SSE-streamed liveManifest
  // takes precedence while a turn is in flight.
  const [preview, setPreview] = useState<ManifestSnapshot | null>(null)

  useEffect(() => {
    let cancelled = false
    const params = new URLSearchParams()
    if (focusEntityId) params.set('focus_entity_id', focusEntityId)
    if (threadId) params.set('thread_id', threadId)
    fetch(`/api/manifest/preview?${params.toString()}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (cancelled) return
        setPreview((d?.manifest as ManifestSnapshot) ?? null)
      })
      .catch(() => { /* drawer just shows empty */ })
    return () => { cancelled = true }
  }, [focusEntityId, threadId])

  const m = liveManifest ?? preview

  return (
    <aside className="drawer">
      <header className="drawer__head">
        <div className="drawer__title">Context</div>
        {onClose && <button className="drawer__close" onClick={onClose} title="Close">×</button>}
      </header>
      {!m && (
        <div className="drawer__empty">
          The agent's loaded context will appear here once a turn runs.
        </div>
      )}
      {m && (
        <div className="drawer__body">
          <Section title="Focus" mono={false}>
            {m.focus ? (
              <>
                <div className="drawer__chip">
                  <span className="drawer__type">{m.focus.entity_type}</span>
                  <span className="drawer__id">{m.focus.entity_id}</span>
                </div>
                <div className="drawer__entity-title">{m.focus.title}</div>
                {m.focus.fields_loaded.length > 0 && (
                  <div className="drawer__fields">
                    {m.focus.fields_loaded.map(f => (
                      <span key={f} className="drawer__field">{f}</span>
                    ))}
                  </div>
                )}
                <pre className="drawer__pre">{m.focus.text}</pre>
              </>
            ) : (
              <div className="drawer__none">No entity focused — workspace scope.</div>
            )}
          </Section>

          <Section title="Thread context">
            {m.thread?.text
              ? <pre className="drawer__pre">{m.thread.text}</pre>
              : <div className="drawer__none">Nothing kept in this thread yet.</div>}
          </Section>

          {m.policy_text && (
            <Section title="Adaptive policy">
              <pre className="drawer__pre">{m.policy_text}</pre>
            </Section>
          )}

          <Section title="Meta">
            <div className="drawer__kv">
              <span className="drawer__k">session</span>
              <span className="drawer__v">{m.session_id}</span>
            </div>
            <div className="drawer__kv">
              <span className="drawer__k">turn</span>
              <span className="drawer__v">{m.turn_index}</span>
            </div>
          </Section>
        </div>
      )}
    </aside>
  )
}

function Section({ title, children, mono = false }: {
  title: string
  children: React.ReactNode
  mono?: boolean
}) {
  return (
    <section className="drawer__section">
      <div className="drawer__section-title">{title}</div>
      <div className={`drawer__section-body ${mono ? 'is-mono' : ''}`}>{children}</div>
    </section>
  )
}
