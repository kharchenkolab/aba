/**
 * Settings — per-project model + the LLM account credential.
 *  - Model: which LLM this project's assistant runs on (spec follows from the
 *    install-wide catalog). Applies to the current project, live next turn.
 *  - Model account: credential status; one field accepts an API key OR a pasted
 *    Claude.ai OAuth token, verified against Anthropic before saving.
 */
import { useCallback, useEffect, useState } from 'react'
import './Settings.css'

interface ModelOption { label: string; model: string; spec: string | null }
interface LlmCurrent { model: string; spec: string | null; label: string | null; pinned: boolean }
interface LlmState { options: ModelOption[]; current: LlmCurrent }

interface CredStatus {
  mode: string
  has_api_key: boolean
  key_suffix: string | null
  has_oauth: boolean
  oauth_source: string | null
  oauth_expires_at: number | null
  valid: boolean
}

interface Props { onClose: () => void }

function credStatusLine(c: CredStatus): string {
  if (c.mode === 'apikey') {
    return c.has_api_key ? `Anthropic API key ••••${c.key_suffix}` : 'No credential set'
  }
  if (!c.has_oauth) return 'Sign-in expired'
  const src = c.oauth_source === 'pasted_token' ? 'Claude.ai token (pasted)'
    : c.oauth_source === 'refreshable_store' ? 'Claude.ai sign-in'
      : 'Claude.ai sign-in'
  const exp = c.oauth_expires_at
    ? ` · expires ${new Date(c.oauth_expires_at * 1000).toLocaleString()}`
    : ''
  return `${src}${exp}`
}

export default function Settings({ onClose }: Props) {
  const [llm, setLlm] = useState<LlmState | null>(null)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const [cred, setCred] = useState<CredStatus | null>(null)
  const [editing, setEditing] = useState(false)
  const [credInput, setCredInput] = useState('')
  const [credBusy, setCredBusy] = useState(false)
  const [credMsg, setCredMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const loadLlm = useCallback(async () => {
    try {
      const r = await fetch('/api/settings/llm')
      if (r.ok) setLlm(await r.json()); else setErr('Could not load model settings.')
    } catch { setErr('Could not load model settings.') }
  }, [])
  const loadCred = useCallback(async () => {
    try {
      const r = await fetch('/api/settings/credential')
      if (r.ok) { const d: CredStatus = await r.json(); setCred(d); setEditing(!d.valid) }
    } catch { /* ignore */ }
  }, [])
  useEffect(() => { loadLlm(); loadCred() }, [loadLlm, loadCred])

  async function pickModel(model: string) {
    if (saving || !llm || model === llm.current.model) return
    setSaving(true); setErr(null)
    try {
      const r = await fetch('/api/settings/llm', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      })
      if (r.ok) { const d = await r.json(); setLlm(s => (s ? { ...s, current: d.current } : s)) }
      else setErr('Could not change the model.')
    } catch { setErr('Could not change the model.') } finally { setSaving(false) }
  }

  async function saveCred() {
    const credential = credInput.trim()
    if (!credential || credBusy) return
    setCredBusy(true); setCredMsg(null)
    try {
      const r = await fetch('/api/settings/credential', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credential }),
      })
      if (r.ok) {
        setCred(await r.json()); setCredInput(''); setEditing(false)
        setCredMsg({ ok: true, text: 'Verified and saved.' })
      } else {
        const d = await r.json().catch(() => ({} as { detail?: string }))
        setCredMsg({ ok: false, text: d.detail || 'Could not save the credential.' })
      }
    } catch { setCredMsg({ ok: false, text: 'Could not save the credential.' }) }
    finally { setCredBusy(false) }
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
                    key={o.model} role="radio" aria-checked={active}
                    className={`model-row ${active ? 'is-active' : ''}`}
                    disabled={saving} onClick={() => pickModel(o.model)}
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

        <section className="settings__section">
          <h3 className="settings__section-title">Model account</h3>
          {!cred ? (
            <div className="settings__empty">Loading…</div>
          ) : !editing ? (
            <div className="cred-status">
              <span className={`cred-status__dot ${cred.valid ? 'is-ok' : 'is-bad'}`} aria-hidden>●</span>
              <span className="cred-status__text">{credStatusLine(cred)}</span>
              <button className="cred-status__change"
                onClick={() => { setCredMsg(null); setEditing(true) }}>Change</button>
            </div>
          ) : (
            <>
              {!cred.valid && (
                <p className="settings__hint">
                  No valid credential — enter your Anthropic API key to continue.
                </p>
              )}
              <div className="cred-row">
                <input
                  type="password" placeholder="Enter API key (sk-ant-…)" autoComplete="off"
                  value={credInput} disabled={credBusy}
                  onChange={e => setCredInput(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') saveCred() }}
                />
                <button disabled={credBusy || !credInput.trim()} onClick={saveCred}>
                  {credBusy ? 'Checking…' : 'Save'}
                </button>
                {cred.valid && !credBusy && (
                  <button className="cred-cancel"
                    onClick={() => { setEditing(false); setCredInput(''); setCredMsg(null) }}>
                    Cancel
                  </button>
                )}
              </div>
              <span className="cred-field__hint">
                An Anthropic API key, or a Claude.ai OAuth token from <code>claude setup-token</code>.
                It's verified with Anthropic before saving.
              </span>
            </>
          )}
          {credMsg && (
            <div className={credMsg.ok ? 'settings__note' : 'settings__error'}>{credMsg.text}</div>
          )}
        </section>
      </div>
    </div>
  )
}
