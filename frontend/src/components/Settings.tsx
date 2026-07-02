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

interface EnvProfile {
  run_python: boolean; run_r: boolean; run_nextflow: boolean
  nextflow_present: boolean; container_engines: string[]; cluster: boolean; gpu: boolean
}
interface EnvState {
  profile: EnvProfile; policy: string; user_pref: string
  counts: { total: number; blocked: number; runnable: number; policy: string }
  options: string[]
}

function envDetail(p: EnvProfile): string {
  const bits = [...p.container_engines]
  if (p.cluster) bits.push('cluster')
  return bits.length ? bits.join(' · ') : 'ready'
}
function envEffectLine(env: EnvState): string {
  const { total, blocked } = env.counts
  if (!blocked) return `All ${total} workflows can run in this workspace.`
  if (env.policy === 'hard') return `${blocked} of ${total} pipeline workflows hidden here (they need a cluster).`
  if (env.policy === 'soft') return `${blocked} of ${total} pipeline workflows de-prioritized here — still searchable; they route to HPC.`
  return `Showing all ${total} workflows, including ${blocked} that can't run in this workspace.`
}

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

  const [env, setEnv] = useState<EnvState | null>(null)
  const [envSaving, setEnvSaving] = useState(false)

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
  const loadEnv = useCallback(async () => {
    try {
      const r = await fetch('/api/settings/environment')
      if (r.ok) setEnv(await r.json())
    } catch { /* ignore */ }
  }, [])
  useEffect(() => { loadLlm(); loadCred(); loadEnv() }, [loadLlm, loadCred, loadEnv])

  async function pickGate(value: string) {
    const cur = env?.user_pref === 'soft' ? 'auto' : (env?.user_pref || 'auto')
    if (envSaving || !env || value === cur) return
    setEnvSaving(true)
    try {
      const r = await fetch('/api/settings/environment', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        // 'auto' clears the pin (revert to default); off/hard are stored as-is
        body: JSON.stringify({ env_gate: value === 'auto' ? '' : value }),
      })
      if (r.ok) loadEnv()
    } catch { /* ignore */ } finally { setEnvSaving(false) }
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
            <select className="settings-select" aria-label="Model" disabled={saving}
              value={llm.current.model} onChange={e => pickModel(e.target.value)}>
              {llm.options.map(o => (
                <option key={o.model} value={o.model}>
                  {o.label}{o.model ? ` — ${o.model}` : ''}
                </option>
              ))}
            </select>
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

        <section className="settings__section">
          <h3 className="settings__section-title">Analysis environment</h3>
          {!env ? (
            <div className="settings__empty">Loading…</div>
          ) : (
            <>
              <p className="settings__hint">What this workspace can run (detected automatically):</p>
              <ul className="env-detected">
                <li><span className="env-ok" aria-hidden>✓</span> In-workspace analysis (Python / R)</li>
                <li>
                  <span className={env.profile.run_nextflow ? 'env-ok' : 'env-no'} aria-hidden>
                    {env.profile.run_nextflow ? '✓' : '✗'}
                  </span>{' '}
                  Pipeline workflows (nf-core)
                  <span className="env-detail">
                    {env.profile.run_nextflow
                      ? ` — ${envDetail(env.profile)}`
                      : ' — no scheduler or container engine here'}
                  </span>
                </li>
              </ul>
              <p className="settings__hint">
                Pipeline workflows are heavy nf-core jobs (variant calling, ChIP/ATAC,
                methylation, metagenomics…) — they need a cluster + containers to run.
              </p>
              <label className="settings__select-label" htmlFor="pipe-gate">
                Suggest pipeline workflows in this workspace:
              </label>
              <select id="pipe-gate" className="settings-select" disabled={envSaving}
                value={env.user_pref === 'soft' ? 'auto' : (env.user_pref || 'auto')}
                onChange={e => pickGate(e.target.value)}>
                <option value="auto">Only when they can run here (recommended)</option>
                <option value="off">Always — even if they need a cluster</option>
                <option value="hard">Never</option>
              </select>
              <p className="settings__hint">{envEffectLine(env)}</p>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
