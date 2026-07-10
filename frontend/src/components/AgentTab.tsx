/**
 * Settings → Agent tab. The frequent control (Model) sits prominently on top;
 * the rare stuff (Provider + Credential) is a calm, status-first Account block
 * below that only expands on Change.
 *
 *  - Provider (Anthropic / OpenAI) is a segmented control that filters the Model
 *    dropdown AND swaps the per-provider credential card.
 *  - Credential: a key/token field (verified before saving) OR a "Subscription"
 *    button that runs the provider's OAuth roundtrip (Claude.ai / ChatGPT-Codex).
 */
import { useCallback, useEffect, useState } from 'react'

type Provider = 'anthropic' | 'openai'
const PROVIDERS: { id: Provider; label: string }[] = [
  { id: 'anthropic', label: 'Anthropic' },
  { id: 'openai', label: 'OpenAI' },
]

interface ModelOption { label: string; model: string; spec: string | null; provider: Provider }
interface LlmCurrent { model: string; spec: string | null; label: string | null; provider: Provider; pinned: boolean }
interface LlmState { options: ModelOption[]; current: LlmCurrent }

interface CredStatus {
  provider: Provider
  mode: string
  has_api_key: boolean
  key_suffix: string | null
  has_oauth: boolean
  oauth_source: string | null
  oauth_expires_at: number | null
  valid: boolean
}

function providerName(p: Provider) { return p === 'openai' ? 'OpenAI' : 'Anthropic' }

function credStatusLine(c: CredStatus): string {
  if (c.has_oauth) {
    const sub = c.provider === 'openai' ? 'ChatGPT / Codex subscription' : 'Claude.ai subscription'
    const exp = c.oauth_expires_at ? ` · expires ${new Date(c.oauth_expires_at * 1000).toLocaleDateString()}` : ''
    return `${sub}${exp}`
  }
  if (c.has_api_key) return `${providerName(c.provider)} API key ••••${c.key_suffix}`
  return 'No credential set'
}

function keyPlaceholder(p: Provider) {
  return p === 'openai' ? 'Enter API key (sk-…)' : 'Enter API key (sk-ant-… or sk-ant-oat…)'
}
function keyHint(p: Provider) {
  return p === 'openai'
    ? 'An OpenAI API key. Verified with OpenAI before saving.'
    : 'An Anthropic API key, or a Claude.ai token from `claude setup-token`. Verified before saving.'
}
function subLabel(p: Provider) {
  return p === 'openai' ? 'Sign in with ChatGPT' : 'Sign in with Claude.ai'
}

export default function AgentTab() {
  const [llm, setLlm] = useState<LlmState | null>(null)
  const [provider, setProvider] = useState<Provider>('anthropic')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const [cred, setCred] = useState<CredStatus | null>(null)
  const [credLoading, setCredLoading] = useState(false)
  const [editing, setEditing] = useState(false)
  const [credInput, setCredInput] = useState('')
  const [credBusy, setCredBusy] = useState(false)
  const [subBusy, setSubBusy] = useState(false)
  const [subFlow, setSubFlow] = useState<string | null>(null)   // active sign-in flow_id
  const [subCode, setSubCode] = useState('')
  const [credMsg, setCredMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const loadCred = useCallback(async (p: Provider) => {
    setCredLoading(true)
    try {
      const r = await fetch(`/api/settings/credential?provider=${p}`)
      if (r.ok) { const d = (await r.json()) as CredStatus; setCred(d); setEditing(!d.valid) }
    } catch { /* ignore */ } finally { setCredLoading(false) }
  }, [])

  // Initial: load model catalog + current, set the provider tab to the active
  // model's provider, then load that provider's credential.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const l = await fetch('/api/settings/llm').then(r => (r.ok ? r.json() : null)).catch(() => null)
      if (cancelled) return
      if (l) {
        setLlm(l)
        const p: Provider = (l.current?.provider as Provider) || 'anthropic'
        setProvider(p)
        loadCred(p)
      } else setErr('Could not load model settings.')
    })()
    return () => { cancelled = true }
  }, [loadCred])

  function switchProvider(p: Provider) {
    if (p === provider) return
    setProvider(p); setCredInput(''); setCredMsg(null); setEditing(false)
    loadCred(p)
  }

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
        body: JSON.stringify({ credential, provider }),
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

  // Subscription sign-in (paste-code model — works locally AND behind a remote/OOD
  // proxy): start the flow, open the provider's sign-in tab, then reveal a code
  // input the user pastes back; submit exchanges it for a token.
  async function startSubscription() {
    if (subBusy) return
    setSubBusy(true); setCredMsg(null)
    try {
      const r = await fetch(`/api/settings/credential/oauth/start?provider=${provider}`, { method: 'POST' })
      if (!r.ok) {
        const d = await r.json().catch(() => ({} as { detail?: string }))
        setCredMsg({ ok: false, text: d.detail || 'Sign-in is unavailable right now.' })
        return
      }
      const { authorize_url, flow_id } = await r.json()
      setSubFlow(flow_id); setSubCode('')
      window.open(authorize_url, '_blank', 'noopener')
    } catch { setCredMsg({ ok: false, text: 'Sign-in failed.' }) }
    finally { setSubBusy(false) }
  }

  async function submitSubscriptionCode() {
    const code = subCode.trim()
    if (!code || !subFlow || subBusy) return
    setSubBusy(true); setCredMsg(null)
    try {
      const r = await fetch('/api/settings/credential/oauth/submit', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ flow_id: subFlow, code }),
      })
      if (r.ok) {
        setCred(await r.json()); setEditing(false); setSubFlow(null); setSubCode('')
        setCredMsg({ ok: true, text: 'Signed in.' })
      } else {
        const d = await r.json().catch(() => ({} as { detail?: string }))
        setCredMsg({ ok: false, text: d.detail || 'Sign-in failed.' })
      }
    } catch { setCredMsg({ ok: false, text: 'Sign-in failed.' }) }
    finally { setSubBusy(false) }
  }

  const models = (llm?.options || []).filter(o => o.provider === provider)
  const activeIsThisProvider = llm?.current.provider === provider

  return (
    <>
      {/* Model — the frequent control, up top */}
      <section className="settings__section">
        <h3 className="settings__section-title">Model</h3>
        <p className="settings__hint">
          The model this project's assistant uses — applies to the current project,
          effective on your next message.
        </p>
        {err && <div className="settings__error">{err}</div>}
        {!llm ? (
          <div className="settings__empty">Loading…</div>
        ) : (
          <>
            <select className="settings-select" aria-label="Model" disabled={saving}
              value={activeIsThisProvider ? llm.current.model : ''}
              onChange={e => pickModel(e.target.value)}>
              {!activeIsThisProvider && <option value="" disabled>Choose a {providerName(provider)} model…</option>}
              {models.map(o => (
                <option key={o.model} value={o.model}>
                  {o.label}{o.model ? ` — ${o.model}` : ''}
                </option>
              ))}
            </select>
            {!activeIsThisProvider && (
              <p className="settings__hint">
                Currently running <strong>{llm.current.label || llm.current.model}</strong>{' '}
                ({providerName(llm.current.provider)}). Pick a {providerName(provider)} model to switch.
              </p>
            )}
          </>
        )}
      </section>

      {/* Account — provider + credential, status-first */}
      <section className="settings__section">
        <h3 className="settings__section-title">Account</h3>

        <div className="provider-seg" role="tablist" aria-label="Provider">
          {PROVIDERS.map(p => (
            <button key={p.id} role="tab" aria-selected={provider === p.id}
              className={`provider-seg__btn ${provider === p.id ? 'is-active' : ''}`}
              onClick={() => switchProvider(p.id)}>{p.label}</button>
          ))}
        </div>

        {credLoading || !cred ? (
          <div className="settings__empty">Loading…</div>
        ) : !editing ? (
          <div className="cred-status">
            <span className={`cred-status__dot ${cred.valid ? 'is-ok' : 'is-bad'}`} aria-hidden>●</span>
            <span className="cred-status__text">{credStatusLine(cred)}</span>
            <button className="cred-status__change"
              onClick={() => { setCredMsg(null); setEditing(true) }}>Change</button>
          </div>
        ) : (
          <div className="cred-edit">
            <div className="cred-edit__label">Connect {providerName(provider)}</div>
            <div className="cred-method">
              <div className="cred-method__title">API key or token</div>
              <div className="cred-row">
                <input
                  type="password" placeholder={keyPlaceholder(provider)} autoComplete="off"
                  value={credInput} disabled={credBusy}
                  onChange={e => setCredInput(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') saveCred() }}
                />
                <button disabled={credBusy || !credInput.trim()} onClick={saveCred}>
                  {credBusy ? 'Checking…' : 'Save'}
                </button>
              </div>
              <span className="cred-field__hint">{keyHint(provider)}</span>
            </div>

            <div className="cred-or"><span>or</span></div>

            <div className="cred-method">
              <div className="cred-method__title">Subscription</div>
              {!subFlow ? (
                <>
                  <button className="cred-sub" disabled={subBusy} onClick={startSubscription}>
                    {subBusy ? 'Opening sign-in…' : `${subLabel(provider)} →`}
                  </button>
                  <span className="cred-field__hint">
                    Use your {provider === 'openai' ? 'ChatGPT / Codex' : 'Claude.ai'} plan — opens a
                    sign-in tab, then paste the code it shows.
                  </span>
                </>
              ) : (
                <>
                  <div className="cred-row">
                    <input
                      type="text" placeholder="Paste the code from the sign-in page" autoComplete="off"
                      value={subCode} disabled={subBusy}
                      onChange={e => setSubCode(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') submitSubscriptionCode() }}
                    />
                    <button disabled={subBusy || !subCode.trim()} onClick={submitSubscriptionCode}>
                      {subBusy ? 'Connecting…' : 'Connect'}
                    </button>
                  </div>
                  <span className="cred-field__hint">
                    Signed in in the other tab? Paste the code shown there.{' '}
                    <button className="cred-linkbtn" onClick={() => { setSubFlow(null); setSubCode('') }}>Cancel</button>
                  </span>
                </>
              )}
            </div>

            {cred.valid && !credBusy && !subBusy && (
              <button className="cred-cancel"
                onClick={() => { setEditing(false); setCredInput(''); setCredMsg(null) }}>
                Cancel
              </button>
            )}
          </div>
        )}
        {credMsg && (
          <div className={credMsg.ok ? 'settings__note' : 'settings__error'}>{credMsg.text}</div>
        )}
      </section>
    </>
  )
}
