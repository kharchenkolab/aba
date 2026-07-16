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
import type { ComputeProposal, PreflightResult, StoragePath } from '../lib/api'

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
  const [newPath, setNewPath] = useState('')
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
    setBusy(true); setError(null); setPre(null)
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
        <strong>Connect a machine</strong>
        <button className="mod-linkbtn" onClick={onCancel}>cancel</button>
      </div>

      {step === 'entry' && (
        <div>
          <label className="cmp-label">
            Address — the same thing you type after <code>ssh</code>
          </label>
          <div className="cmp-addr">
            <input value={dest} placeholder="me@login.cluster.edu" spellCheck={false}
              list="cmp-saved-hosts" autoFocus
              onChange={e => setDest(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && dest.trim()) runPreflight() }} />
            <datalist id="cmp-saved-hosts">
              {hosts.map(h => (
                <option key={h.host}
                  value={h.user && h.hostname ? `${h.user}@${h.hostname}` : h.host}>
                  {h.host}
                </option>
              ))}
            </datalist>
            <button className="cmp-btn cmp-btn--primary" disabled={!dest.trim() || busy}
              onClick={() => runPreflight()}>{busy ? 'Checking…' : 'Continue'}</button>
          </div>
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
                      onClick={() => runPreflight()}>{busy ? 'Testing…' : 'Test again'}</button>
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
                  onClick={() => runPreflight()}>{busy ? 'Testing…' : 'Test again'}</button>
                <button className="cmp-btn" onClick={() => setStep('entry')}>Back</button>
              </div>
            </div>
          )}
          {error && <div className="cmp-note">{error}</div>}
        </div>
      )}

      {(step === 'probing' || step === 'connecting') && (
        <div>
          <p>{step === 'probing'
            ? 'Looking at the machine — this takes a few seconds…'
            : 'Connecting…'}</p>
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
            <input value={proposal.name} spellCheck={false}
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
              <input value={proposal.working.root} spellCheck={false}
                onChange={e => patch({ working: { ...proposal.working, root: e.target.value } })} />
              <div className="cmp-dim">
                {proposal.working.free_gb != null ? `${proposal.working.free_gb} GB free — ` : ''}
                {proposal.working.reason}
              </div>
            </div>

            <label>Long-term store</label>
            <div>
              {proposal.long_term.map((p: StoragePath, i: number) => (
                <div key={p.path} className="cmp-paths">
                  <code>{p.path}</code>
                  <button className="mod-linkbtn" onClick={() =>
                    patch({ long_term: proposal.long_term.filter((_, j) => j !== i) })}>remove</button>
                </div>
              ))}
              <div className="cmp-addpath">
                <input value={newPath} placeholder="/groups/lab" spellCheck={false}
                  onChange={e => setNewPath(e.target.value)} />
                <button className="cmp-btn" disabled={!newPath.trim()}
                  onClick={() => {
                    patch({ long_term: [...proposal.long_term, { path: newPath.trim(), stable: true }] })
                    setNewPath('')
                  }}>Add</button>
              </div>
              <div className="cmp-dim">data read in place; results kept here stay put</div>
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
              onClick={connect}>Connect</button>
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
