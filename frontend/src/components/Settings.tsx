/**
 * Settings — per-project assistant configuration + account.
 *  - Model: which LLM this project's assistant runs on (spec follows from the
 *    install-wide catalog). Applies to the current project, live next turn.
 *  - Account: LLM credential status, replace API key, paste Claude.ai OAuth token.
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
}

interface Props { onClose: () => void }

export default function Settings({ onClose }: Props) {
  const [llm, setLlm] = useState<LlmState | null>(null)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const [cred, setCred] = useState<CredStatus | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [oauthTok, setOauthTok] = useState('')
  const [credBusy, setCredBusy] = useState(false)
  const [credMsg, setCredMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const loadLlm = useCallback(async () => {
    try {
      const r = await fetch('/api/settings/llm')
      if (r.ok) setLlm(await r.json())
      else setErr('Could not load model settings.')
    } catch { setErr('Could not load model settings.') }
  }, [])
  const loadCred = useCallback(async () => {
    try { const r = await fetch('/api/settings/credential'); if (r.ok) setCred(await r.json()) } catch { /* ignore */ }
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

  async function saveCred(path: string, body: object, okText: string) {
    if (credBusy) return
    setCredBusy(true); setCredMsg(null)
    try {
      const r = await fetch(path, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (r.ok) {
        setCred(await r.json()); setApiKey(''); setOauthTok('')
        setCredMsg({ ok: true, text: okText })
      } else {
        const d = await r.json().catch(() => ({} as { detail?: string }))
        setCredMsg({ ok: false, text: d.detail || 'Could not save the credential.' })
      }
    } catch { setCredMsg({ ok: false, text: 'Could not save the credential.' }) }
    finally { setCredBusy(false) }
  }

  const credLine = !cred ? '' : cred.mode === 'apikey'
    ? (cred.has_api_key ? `Using an Anthropic API key (••••${cred.key_suffix}).` : 'No credential set.')
    : `Using Claude.ai sign-in (${cred.mode})`
      + (cred.oauth_expires_at ? `, expires ${new Date(cred.oauth_expires_at * 1000).toLocaleString()}` : '')
      + '.'

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
          <h3 className="settings__section-title">Account</h3>
          <p className="settings__hint">{credLine || 'Loading…'}</p>
          {credMsg && (
            <div className={credMsg.ok ? 'settings__note' : 'settings__error'}>{credMsg.text}</div>
          )}
          <label className="cred-field">
            <span className="cred-field__label">Anthropic API key</span>
            <div className="cred-row">
              <input type="password" placeholder="sk-ant-…" autoComplete="off" value={apiKey}
                onChange={e => setApiKey(e.target.value)} />
              <button disabled={credBusy || !apiKey.trim()}
                onClick={() => saveCred('/api/settings/credential/apikey', { key: apiKey.trim() }, 'API key updated.')}>
                Save
              </button>
            </div>
          </label>
          <label className="cred-field">
            <span className="cred-field__label">Claude.ai OAuth token</span>
            <div className="cred-row">
              <input type="password" placeholder="sk-ant-oat…" autoComplete="off" value={oauthTok}
                onChange={e => setOauthTok(e.target.value)} />
              <button disabled={credBusy || !oauthTok.trim()}
                onClick={() => saveCred('/api/settings/credential/oauth', { token: oauthTok.trim() }, 'Signed in with Claude.ai.')}>
                Save
              </button>
            </div>
            <span className="cred-field__hint">
              Paste a token from <code>claude setup-token</code>. The browser sign-in flow lives in the desktop app.
            </span>
          </label>
        </section>
      </div>
    </div>
  )
}
