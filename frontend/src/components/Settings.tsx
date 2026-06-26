/**
 * Settings — per-project assistant configuration.
 *  - Model: which LLM this project's assistant runs on (the agent spec follows
 *    from the install-wide catalog). Applies to the current project, live next turn.
 *  - Account: API key / sign-in (added next).
 */
import { useCallback, useEffect, useState } from 'react'
import './Settings.css'

interface ModelOption { label: string; model: string; spec: string | null }
interface LlmCurrent { model: string; spec: string | null; label: string | null; pinned: boolean }
interface LlmState { options: ModelOption[]; current: LlmCurrent }

interface Props { onClose: () => void }

export default function Settings({ onClose }: Props) {
  const [llm, setLlm] = useState<LlmState | null>(null)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const loadLlm = useCallback(async () => {
    try {
      const r = await fetch('/api/settings/llm')
      if (r.ok) setLlm(await r.json())
      else setErr('Could not load model settings.')
    } catch { setErr('Could not load model settings.') }
  }, [])
  useEffect(() => { loadLlm() }, [loadLlm])

  async function pickModel(model: string) {
    if (saving || !llm || model === llm.current.model) return
    setSaving(true); setErr(null)
    try {
      const r = await fetch('/api/settings/llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      })
      if (r.ok) { const d = await r.json(); setLlm(s => (s ? { ...s, current: d.current } : s)) }
      else setErr('Could not change the model.')
    } catch { setErr('Could not change the model.') } finally { setSaving(false) }
  }

  return (
    <div className="settings-backdrop" onClick={onClose}>
      <div className="settings" onClick={e => e.stopPropagation()}>
        <div className="settings__head">
          <h2>Settings</h2>
          <button onClick={onClose} className="settings__close" title="Close">×</button>
        </div>

        <section className="settings__section">
          <h3 className="settings__section-title">Model</h3>
          <p className="settings__hint">
            The model this project's assistant uses. It applies to the current project
            and takes effect on your next message.
          </p>
          {err && <div className="settings__error">{err}</div>}
          {!llm ? (
            <div className="settings__empty">Loading…</div>
          ) : (
            <div className="model-list" role="radiogroup" aria-label="Model">
              {llm.options.map(o => {
                const active = o.model === llm.current.model
                return (
                  <button
                    key={o.model}
                    role="radio"
                    aria-checked={active}
                    className={`model-row ${active ? 'is-active' : ''}`}
                    disabled={saving}
                    onClick={() => pickModel(o.model)}
                  >
                    <span className="model-row__radio" aria-hidden>{active ? '●' : '○'}</span>
                    <span className="model-row__label">{o.label}</span>
                    <span className="model-row__id">{o.model}</span>
                  </button>
                )
              })}
            </div>
          )}
        </section>

        {/* Account section (API key / sign-in) is added next. */}
      </div>
    </div>
  )
}
