/**
 * Settings → Environment tab. What this workspace can run (detected) + the
 * pipeline-suggestion gate. Moved verbatim from the old single-panel Settings;
 * behavior unchanged.
 */
import { useCallback, useEffect, useState } from 'react'

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

export default function EnvironmentTab() {
  const [env, setEnv] = useState<EnvState | null>(null)
  const [envSaving, setEnvSaving] = useState(false)
  // Analysis-module install status ("Setting up…") now lives in Settings → Modules.

  const loadEnv = useCallback(async () => {
    try {
      const r = await fetch('/api/settings/environment')
      if (r.ok) setEnv(await r.json())
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { loadEnv() }, [loadEnv])

  async function pickGate(value: string) {
    const cur = env?.user_pref === 'soft' ? 'auto' : (env?.user_pref || 'auto')
    if (envSaving || !env || value === cur) return
    setEnvSaving(true)
    try {
      const r = await fetch('/api/settings/environment', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ env_gate: value === 'auto' ? '' : value }),
      })
      if (r.ok) loadEnv()
    } catch { /* ignore */ } finally { setEnvSaving(false) }
  }

  return (
    <>
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
          <label className="settings__select-label" htmlFor="pipe-gate">
            Suggest pipeline workflows in this workspace:
          </label>
          <select id="pipe-gate" className="settings-select" disabled={envSaving}
            value={env.user_pref === 'soft' ? 'auto' : (env.user_pref || 'auto')}
            onChange={e => pickGate(e.target.value)}>
            <option value="auto">Only when they can run here (recommended)</option>
            <option value="off">Always (even where they can't run)</option>
            <option value="hard">Never</option>
          </select>
          <p className="settings__hint">{envEffectLine(env)}</p>
        </>
      )}
    </section>
    </>
  )
}
