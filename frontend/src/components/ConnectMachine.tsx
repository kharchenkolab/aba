/**
 * Connect a machine (misc/compute_settings.md §5): one screen, three moments —
 * tell me the machine → aba figures it out → confirm.
 *
 *   entry    the ssh address (or a saved host / lab template) — the ONLY input
 *   access   classified preflight: host-key confirm (real fingerprint),
 *            key setup (aba NEVER sees the password — the ssh-copy-id line
 *            runs in the user's own terminal), or the plain VPN retry
 *   probe    weft probes; narration arrives via aba:compute events
 *   proposal "here's what I found and how I'd set it up" — every line editable
 *   connect  register + background queue verification (never blocks)
 */
import { useEffect, useRef, useState } from 'react'
import { computeApi } from '../lib/api'
import type { ComputeProposal, PreflightResult } from '../lib/api'

type Step = 'entry' | 'access' | 'probing' | 'proposal' | 'connecting'

interface Props {
  knownNames: string[]
  onDone: () => void
  onCancel: () => void
}

export default function ConnectMachine({ knownNames, onDone, onCancel }: Props) {
  const [step, setStep] = useState<Step>('entry')
  const [dest, setDest] = useState('')
  const [hosts, setHosts] = useState<{ host: string; hostname?: string; user?: string }[]>([])
  const [templates, setTemplates] = useState<{ name: string; dest?: string; note?: string }[]>([])
  const [pre, setPre] = useState<PreflightResult | null>(null)
  const [keyCmd, setKeyCmd] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [narration, setNarration] = useState<string[]>([])
  const [proposal, setProposal] = useState<ComputeProposal | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [customRoot, setCustomRoot] = useState(false)
  const proposalName = useRef<string>('')

  useEffect(() => {
    computeApi.hosts().then(r => setHosts(r.hosts)).catch(() => {})
    computeApi.templates().then(r => setTemplates(r.templates)).catch(() => {})
  }, [])

  // registration narration: weft bootstrap.step events relayed onto the bus
  useEffect(() => {
    const onEv = (e: Event) => {
      const ev = (e as CustomEvent).detail as
        { site?: string; phase?: string; step?: string; note?: string }
      if (!proposalName.current || ev.site !== proposalName.current) return
      const line = ev.note || ev.step || ev.phase
      if (line) setNarration(ns => ns.includes(line) ? ns : [...ns, line])
    }
    window.addEventListener('aba:compute', onEv)
    return () => window.removeEventListener('aba:compute', onEv)
  }, [])

  async function runPreflight(currentDest = dest) {
    // NOTE: keep the previous `pre` while retrying — clearing it blanks the
    // access card mid-"Test again" (leaving the user staring at an empty box)
    setBusy(true); setError(null)
    try {
      const r = await computeApi.preflight({ dest: currentDest.trim() })
      setPre(r)
      if (r.case === 'ok') await runProbe(currentDest)
      else setStep('access')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e)); setStep('entry')
    } finally { setBusy(false) }
  }

  async function runProbe(currentDest = dest) {
    setStep('probing'); setError(null)
    setNarration([`✓ reached ${currentDest.trim()}`])
    try {
      const r = await computeApi.probe({ dest: currentDest.trim() })
      proposalName.current = r.proposal.name
      setProposal(r.proposal)
      setNarration(ns => [...ns, `✓ ${r.proposal.headline}`])
      setStep('proposal')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e)); setStep('access')
    }
  }

  async function acceptKeyAndRetry() {
    if (!pre?.hostkey) return
    setBusy(true)
    try {
      await computeApi.acceptHostkey(pre.hostkey.line)
      await runPreflight()
    } finally { setBusy(false) }
  }

  async function setupKey() {
    setBusy(true)
    try {
      const r = await computeApi.keysetup({ dest: dest.trim() })
      setKeyCmd(r.command)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }

  async function connect() {
    if (!proposal) return
    setBusy(true); setError(null)
    proposalName.current = proposal.name
    setNarration([]); setStep('connecting')
    try {
      await computeApi.connect({ dest: dest.trim(), proposal })
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e)); setStep('proposal')
    } finally { setBusy(false) }
  }

  const patch = (p: Partial<ComputeProposal>) =>
    setProposal(prev => prev ? { ...prev, ...p } : prev)

  return (
    <div className="cmp-connect">
      <div className="cmp-connect__head">
        <strong>Add a machine</strong>
        <button className="mod-linkbtn" onClick={onCancel}>cancel</button>
      </div>

      {step === 'entry' && (
        <div>
          <label className="cmp-label">
            Address — the same thing you type after <code>ssh</code>
          </label>
          <div className="cmp-addr">
            {/* deliberately NO <datalist>: Chrome bolts its own address/email
                autofill (+ "Manage addresses") onto datalist inputs and
                ignores autocomplete=off there — saved hosts render as plain
                chips below instead */}
            <input value={dest} placeholder="me@login.cluster.edu" spellCheck={false}
              autoFocus autoComplete="off" name="cmp-ssh-dest" data-1p-ignore
              onChange={e => setDest(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && dest.trim()) runPreflight() }} />
            <button className="cmp-btn cmp-btn--primary" disabled={!dest.trim() || busy}
              onClick={() => runPreflight()}>
              {busy ? <><span className="cmp-spin" aria-hidden>◜</span> Checking…</> : 'Continue'}
            </button>
          </div>
          {hosts.length > 0 && (
            <div className="cmp-templates">
              <span className="cmp-dim">or pick a saved host:</span>
              {/* native <select>, not <datalist>: Chrome bolts address/email
                  autofill onto datalist inputs and ignores autocomplete=off */}
              <select value="" onChange={e => { if (e.target.value) setDest(e.target.value) }}>
                <option value="">~/.ssh/config…</option>
                {hosts.slice(0, 20).map(h => {
                  const v = h.user && h.hostname ? `${h.user}@${h.hostname}` : h.host
                  return <option key={h.host} value={v}>{h.host}</option>
                })}
              </select>
            </div>
          )}
          {templates.length > 0 && (
            <div className="cmp-templates">
              Your lab uses:{' '}
              {templates.map(t => (
                <button key={t.name} className="mod-linkbtn" title={t.note}
                  onClick={() => { if (t.dest) { setDest(t.dest); runPreflight(t.dest) } }}>
                  {t.name}
                </button>
              ))}
            </div>
          )}
          <p className="cmp-dim">This machine is already connected. Cloud: coming later.</p>
        </div>
      )}

      {step === 'access' && pre && (
        <div>
          <p className="cmp-dim">
            machine: <code>{dest.trim()}</code>
            {busy && <>{' '}· <span className="cmp-spin" aria-hidden>◜</span> testing the connection…</>}
          </p>
          {pre.case === 'hostkey' && pre.hostkey ? (
            <div>
              <p>First time connecting to <code>{dest.trim()}</code>. The machine
                identifies itself as</p>
              <p><code className="cmp-fp">{pre.hostkey.fingerprint}</code>{' '}
                <span className="cmp-dim">({pre.hostkey.keytype})</span></p>
              <p>If this is your machine, accept — aba will remember it.</p>
              <div className="cmp-actions">
                <button className="cmp-btn cmp-btn--primary" disabled={busy}
                  onClick={acceptKeyAndRetry}>Accept and continue</button>
                <button className="cmp-btn" onClick={() => setStep('entry')}>Back</button>
              </div>
            </div>
          ) : pre.case === 'auth' ? (
            <div>
              <p>The machine answered, but aba can’t sign in without a password.
                Let’s set up key access (one-time, ~1 minute):</p>
              {!keyCmd ? (
                <button className="cmp-btn cmp-btn--primary" disabled={busy}
                  onClick={setupKey}>Create a key for this machine</button>
              ) : (
                <div>
                  <p>Run this in <strong>your own terminal</strong> — it asks for your
                    password once, directly from ssh (aba never sees it):</p>
                  <div className="cmp-cmd">
                    <code>{keyCmd}</code>
                    <button className="mod-linkbtn" onClick={() => {
                      navigator.clipboard?.writeText(keyCmd)
                      setCopied(true); setTimeout(() => setCopied(false), 1500)
                    }}>{copied ? 'copied ✓' : 'copy'}</button>
                  </div>
                  <div className="cmp-actions">
                    <button className="cmp-btn cmp-btn--primary" disabled={busy}
                      onClick={() => runPreflight()}>{busy ? <><span className="cmp-spin" aria-hidden>◜</span> Testing…</> : 'Test again'}</button>
                    <button className="cmp-btn" onClick={() => setStep('entry')}>Back</button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div>
              <p>{pre.cause || 'Couldn’t reach the machine.'}</p>
              {pre.stderr && <details><summary>full message</summary>
                <pre className="cmp-stderr">{pre.stderr}</pre></details>}
              <div className="cmp-actions">
                <button className="cmp-btn cmp-btn--primary" disabled={busy}
                  onClick={() => runPreflight()}>{busy ? <><span className="cmp-spin" aria-hidden>◜</span> Testing…</> : 'Test again'}</button>
                <button className="cmp-btn" onClick={() => setStep('entry')}>Back</button>
              </div>
            </div>
          )}
          {error && <div className="cmp-note">{error}</div>}
        </div>
      )}

      {(step === 'probing' || step === 'connecting') && (
        <div>
          <p>
            <span className="cmp-spin" aria-hidden>◜</span>{' '}
            {step === 'probing'
              ? 'Looking at the machine — this takes a few seconds…'
              : 'Adding the machine…'}
          </p>
          <ul className="cmp-narration">
            {narration.map(n => <li key={n}>{n}</li>)}
          </ul>
        </div>
      )}

      {step === 'proposal' && proposal && (
        <div>
          <p><strong>{proposal.headline}</strong> — here’s how I’d set it up:</p>

          <div className="cmp-form">
            <label>Name</label>
            <input value={proposal.name} spellCheck={false} autoComplete="off"
              name="cmp-site-name" data-1p-ignore
              onChange={e => { patch({ name: e.target.value }) }} />

            <label>Used for</label>
            <div className="cmp-chips">
              {proposal.use_for.map(u => (
                <span key={u} className="cmp-chip is-on">{
                  u === 'interactive' ? 'interactive analysis'
                    : u === 'background' ? 'background jobs' : 'GPU work'}</span>
              ))}
            </div>

            <label>Working space</label>
            <div>
              {(proposal.working.options?.length ?? 0) > 0 ? (
                <select value={customRoot ? '__custom__' : proposal.working.root}
                  onChange={e => {
                    if (e.target.value === '__custom__') { setCustomRoot(true); return }
                    setCustomRoot(false)
                    const opt = proposal.working.options!.find(o => o.root === e.target.value)
                    patch({ working: { ...proposal.working, root: e.target.value,
                                       free_gb: opt?.free_gb, reason: opt?.note },
                            durable: opt?.kind === 'home' })   // re-guess; still editable below
                  }}>
                  {proposal.working.options!.map(o => (
                    <option key={o.root} value={o.root}>
                      {o.root}{o.free_gb != null ? ` — ${o.free_gb} GB free` : ''}
                      {o.note ? ` · ${o.note}` : ''}
                    </option>
                  ))}
                  <option value="__custom__">another path…</option>
                </select>
              ) : null}
              {(customRoot || !(proposal.working.options?.length)) && (
                <input value={proposal.working.root} spellCheck={false} autoComplete="off"
                  onChange={e => patch({ working: { ...proposal.working, root: e.target.value } })} />
              )}
              <label className="cmp-part">
                <input type="checkbox" checked={!!proposal.durable}
                  onChange={e => patch({ durable: e.target.checked })} />
                durable storage
                <span className="cmp-dim">— backed up / not auto-purged; results
                you keep on this machine stay here</span>
              </label>
            </div>

            <label>Notes <span className="cmp-dim">(guidance for scheduling)</span></label>
            <div>
              <textarea className="cmp-notes" rows={2} spellCheck={false}
                placeholder={'e.g. "use only on nights, EU time" — the agent sees this with every plan'}
                value={(proposal.notes ?? []).join('\n')}
                onChange={e => patch({ notes: e.target.value.split('\n') })} />
            </div>

            <label>Files</label>
            <div className="cmp-dim">
              {proposal.contract === 'shared-fs'
                ? `aba sees your files directly (shared filesystem${proposal.contract_evidence?.length ? `: ${proposal.contract_evidence[0]}` : ''})`
                : 'no shared files seen from here — aba will ship work over (not yet supported; connect is limited to shared-storage machines for now)'}
            </div>

            {proposal.partitions.length > 0 && <>
              <label>Job queues <span className="cmp-dim">(Slurm partitions)</span></label>
              <div>
                {proposal.partitions.map((p, i) => (
                  <label key={p.name} className="cmp-part">
                    <input type="checkbox" checked={!!p.selected}
                      onChange={e => {
                        const parts = proposal.partitions.slice()
                        parts[i] = { ...p, selected: e.target.checked }
                        patch({ partitions: parts })
                      }} />
                    <code>{p.name}</code>
                    <span className="cmp-dim">
                      {p.nodes} nodes · {p.cpus_per_node} cpus · {p.mem_gb_per_node} GB
                      {p.gpus_per_node ? ` · ${p.gpus_per_node}× GPU` : ''}
                      {p.max_walltime ? ` · up to ${p.max_walltime}` : ''}
                    </span>
                  </label>
                ))}
              </div>
            </>}

            {(proposal.accounts?.length ?? 0) > 0 && <>
              <label>Billing account</label>
              <select value={proposal.account ?? ''}
                onChange={e => patch({ account: e.target.value || null })}>
                <option value="">(none)</option>
                {proposal.accounts!.map(a => <option key={a} value={a}>{a}</option>)}
              </select>
            </>}
          </div>

          <div className="cmp-actions">
            <button className="cmp-btn cmp-btn--primary"
              disabled={busy || !proposal.name.trim()
                || knownNames.includes(proposal.name.trim())}
              onClick={connect}>
              {busy && <span className="cmp-spin" aria-hidden>◜</span>}
              Add
            </button>
            {knownNames.includes(proposal.name.trim()) &&
              <span className="cmp-dim">that name is taken</span>}
            <button className="cmp-btn" onClick={onCancel}>Cancel</button>
          </div>
          {error && <div className="cmp-note">{error}</div>}
        </div>
      )}
    </div>
  )
}
