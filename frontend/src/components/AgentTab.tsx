/**
 * Settings → Agent tab. Optimized for the common case (stay on the credentialed
 * provider, just switch models) and stable in height so it doesn't jump as you poke it.
 *
 *  - MODEL is the hero: a dropdown showing only models the CURRENT credential can run
 *    (subscription → Codex gpt-5.x; API key → gpt-4o/4.1; Anthropic → all), plus a
 *    fixed-height status line that background-pings the picked model (✓ Ready / ✗ why).
 *  - PROVIDER + CREDENTIAL are rare, so they live in a compact one-line summary with a
 *    collapsible "Manage" panel (auto-opens only when the credential is missing/invalid).
 */
import { useCallback, useEffect, useRef, useState } from 'react'

type Provider = 'anthropic' | 'openai'
const PROVIDERS: { id: Provider; label: string }[] = [
  { id: 'anthropic', label: 'Anthropic' },
  { id: 'openai', label: 'OpenAI' },
]
type Via = 'apikey' | 'subscription' | 'any'
type CredMode = 'subscription' | 'apikey' | 'none'

interface ModelOption { label: string; model: string; spec: string | null; provider: Provider; via: Via }
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

type Ping = { state: 'idle' | 'checking' | 'ok' | 'bad'; detail?: string }

function providerName(p: Provider) { return p === 'openai' ? 'OpenAI' : 'Anthropic' }

function credMode(c: CredStatus | null): CredMode {
  if (c?.has_oauth) return 'subscription'
  if (c?.has_api_key) return 'apikey'
  return 'none'
}
// Only show models the current credential can actually run. `any` (Anthropic) always
// shows; with no credential yet we show everything so the user can see the options.
function modelUsable(o: ModelOption, mode: CredMode): boolean {
  return o.via === 'any' || mode === 'none' || o.via === mode
}

function credStatusLine(c: CredStatus | null): string {
  if (!c) return 'Not connected'
  if (c.has_oauth) {
    const sub = c.provider === 'openai' ? 'ChatGPT / Codex subscription' : 'Claude.ai subscription'
    const exp = c.oauth_expires_at ? ` · expires ${new Date(c.oauth_expires_at * 1000).toLocaleDateString()}` : ''
    return `${sub}${exp}`
  }
  if (c.has_api_key) return `API key ••••${c.key_suffix}`
  return 'Not connected'
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
  const [ping, setPing] = useState<Ping>({ state: 'idle' })
  const pingSeq = useRef(0)

  const [cred, setCred] = useState<CredStatus | null>(null)
  const [manageOpen, setManageOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [credInput, setCredInput] = useState('')
  const [credBusy, setCredBusy] = useState(false)
  const [subBusy, setSubBusy] = useState(false)
  const [subFlow, setSubFlow] = useState<string | null>(null)   // active paste-flow id
  const [subCode, setSubCode] = useState('')
  const [subWaiting, setSubWaiting] = useState(false)           // callback-flow polling
  const [credMsg, setCredMsg] = useState<{ ok: boolean; text: string } | null>(null)

  // Background model check — confirms the current credential can run `model`. Guarded
  // by a sequence so a slow earlier ping can't overwrite a newer selection's result.
  const pingModel = useCallback(async (model: string) => {
    const seq = ++pingSeq.current
    if (!model) { setPing({ state: 'idle' }); return }
    setPing({ state: 'checking' })
    try {
      const r = await fetch('/api/settings/llm/ping', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      })
      const d = await r.json().catch(() => ({ ok: false }))
      if (seq !== pingSeq.current) return
      setPing(d.ok ? { state: 'ok' } : { state: 'bad', detail: d.detail || 'This model may not work with your credential.' })
    } catch {
      if (seq === pingSeq.current) setPing({ state: 'idle' })
    }
  }, [])

  const loadCred = useCallback(async (p: Provider) => {
    try {
      const r = await fetch(`/api/settings/credential?provider=${p}`)
      if (r.ok) {
        const d = (await r.json()) as CredStatus
        setCred(d)
        setEditing(!d.valid)
        if (!d.valid) setManageOpen(true)   // surface a broken/absent credential
        return d
      }
    } catch { /* ignore */ }
    return null
  }, [])

  // Initial (also = "on open", since the tab mounts fresh each time Settings opens):
  // load the catalog + current model, sync the provider, load that credential, and
  // ping the running model.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const l = await fetch('/api/settings/llm').then(r => (r.ok ? r.json() : null)).catch(() => null)
      if (cancelled) return
      if (!l) { setErr('Could not load model settings.'); return }
      setLlm(l)
      const p: Provider = (l.current?.provider as Provider) || 'anthropic'
      setProvider(p)
      loadCred(p)
      pingModel(l.current?.model)
    })()
    return () => { cancelled = true }
  }, [loadCred, pingModel])

  function switchProvider(p: Provider) {
    if (p === provider) return
    setProvider(p); setCredInput(''); setCredMsg(null); setEditing(false); setSubFlow(null)
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
      if (r.ok) {
        const d = await r.json()
        setLlm(s => (s ? { ...s, current: d.current } : s))
        pingModel(model)
      } else setErr('Could not change the model.')
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
        if (llm?.current.provider === provider) pingModel(llm.current.model)
      } else {
        const d = await r.json().catch(() => ({} as { detail?: string }))
        setCredMsg({ ok: false, text: d.detail || 'Could not save the credential.' })
      }
    } catch { setCredMsg({ ok: false, text: 'Could not save the credential.' }) }
    finally { setCredBusy(false) }
  }

  // Subscription sign-in. Callback flow (OpenAI/Codex): ABA captures the code at a
  // localhost callback — poll for it. Paste flow (Anthropic): reveal a code input.
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
      const { authorize_url, flow_id, mode } = await r.json()
      window.open(authorize_url, '_blank', 'noopener')
      if (mode === 'callback') {
        setSubWaiting(true)
        for (let i = 0; i < 150; i++) {
          await new Promise(res => setTimeout(res, 2000))
          const pr = await fetch(`/api/settings/credential/oauth/poll?flow_id=${flow_id}`)
          if (!pr.ok) continue
          const st = await pr.json()
          if (st.state === 'done') {
            setCred(st.credential); setEditing(false); setSubWaiting(false)
            setCredMsg({ ok: true, text: 'Signed in.' })
            if (llm?.current.provider === provider) pingModel(llm.current.model)
            return
          }
          if (st.state === 'error') {
            setSubWaiting(false); setCredMsg({ ok: false, text: st.detail || 'Sign-in failed.' }); return
          }
        }
        setSubWaiting(false); setCredMsg({ ok: false, text: 'Sign-in timed out — try again.' })
      } else {
        setSubFlow(flow_id); setSubCode('')
      }
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
        if (llm?.current.provider === provider) pingModel(llm.current.model)
      } else {
        const d = await r.json().catch(() => ({} as { detail?: string }))
        setCredMsg({ ok: false, text: d.detail || 'Sign-in failed.' })
      }
    } catch { setCredMsg({ ok: false, text: 'Sign-in failed.' }) }
    finally { setSubBusy(false) }
  }

  const mode = credMode(cred)
  const providerModels = (llm?.options || []).filter(o => o.provider === provider)
  const usableModels = providerModels.filter(o => modelUsable(o, mode))
  const models = usableModels.length ? usableModels : providerModels  // never empty the picker
  const activeIsThisProvider = !!llm && llm.current.provider === provider
  const dotClass = cred?.valid ? 'is-ok' : 'is-bad'

  return (
    <>
      {/* MODEL — the frequent control, up top */}
      <section className="settings__section agent-model">
        <h3 className="settings__section-title">Model</h3>
        <p className="settings__hint">
          The model this project's assistant uses — effective on your next message.
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

            {/* Fixed-height status line — reserves its space so the panel never jumps */}
            <div className={`model-status is-${activeIsThisProvider ? ping.state : 'other'}`} aria-live="polite">
              {!activeIsThisProvider ? (
                <span className="model-status__text">
                  Running <strong>{llm.current.label || llm.current.model}</strong> ({providerName(llm.current.provider)}).
                  Pick a {providerName(provider)} model to switch.
                </span>
              ) : ping.state === 'checking' ? (
                <><span className="model-status__spin" aria-hidden>◜</span><span className="model-status__text">Checking model…</span></>
              ) : ping.state === 'ok' ? (
                <><span className="model-status__ico" aria-hidden>✓</span><span className="model-status__text">Ready</span></>
              ) : ping.state === 'bad' ? (
                <><span className="model-status__ico" aria-hidden>✗</span><span className="model-status__text">{ping.detail}</span></>
              ) : (
                <span className="model-status__text">&nbsp;</span>
              )}
            </div>
          </>
        )}
      </section>

      {/* PROVIDER + CREDENTIAL — rare, so a compact summary with a collapsible panel */}
      <section className="settings__section agent-account">
        <div className="agent-summary">
          <span className={`agent-summary__dot ${dotClass}`} aria-hidden>●</span>
          <span className="agent-summary__text">
            <strong>{providerName(provider)}</strong>
            <span className="agent-summary__sep"> · </span>
            {credStatusLine(cred)}
          </span>
          <button className="agent-summary__toggle" aria-expanded={manageOpen}
            onClick={() => { setCredMsg(null); setManageOpen(o => !o) }}>
            {manageOpen ? 'Done' : 'Manage'}
          </button>
        </div>

        {manageOpen && (
          <div className="agent-manage">
            <label className="settings__select-label" htmlFor="provider-select">Provider</label>
            <select id="provider-select" className="settings-select" value={provider}
              onChange={e => switchProvider(e.target.value as Provider)}>
              {PROVIDERS.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>

            <label className="settings__select-label">Credential</label>
            {!cred ? (
              <div className="settings__empty">Loading…</div>
            ) : !editing ? (
              <div className="cred-status">
                <span className={`cred-status__dot ${dotClass}`} aria-hidden>●</span>
                <span className="cred-status__text">{credStatusLine(cred)}</span>
                <button className="cred-status__change"
                  onClick={() => { setCredMsg(null); setEditing(true) }}>Change</button>
              </div>
            ) : (
              <div className="cred-edit">
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
                  {subWaiting ? (
                    <span className="cred-field__hint">
                      Waiting for sign-in… complete it in the other tab, then return here.
                    </span>
                  ) : !subFlow ? (
                    <>
                      <button className="cred-sub" disabled={subBusy} onClick={startSubscription}>
                        {subBusy ? 'Opening sign-in…' : `${subLabel(provider)} →`}
                      </button>
                      <span className="cred-field__hint">
                        Use your {provider === 'openai' ? 'ChatGPT / Codex' : 'Claude.ai'} plan — opens a
                        sign-in tab{provider === 'openai' ? '; ABA captures the result automatically.' : ', then paste the code it shows.'}
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
          </div>
        )}
      </section>
    </>
  )
}
