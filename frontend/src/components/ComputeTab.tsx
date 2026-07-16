/**
 * Settings → Compute (misc/compute_settings.md §4/§6): where analyses run.
 * A status surface first — cards lead with what each machine is for and
 * whether it would take work right now — and a settings form second.
 * Plain labels carry the precise term alongside (layered, not dumbed down).
 * Connect flow lives in ConnectMachine.tsx.
 */
import { useCallback, useEffect, useState } from 'react'
import './ComputeTab.css'
import { computeApi } from '../lib/api'
import type { ComputeSite, StoragePath } from '../lib/api'
import { withBasePath } from '../oodBase'
import ConnectMachine from './ConnectMachine'

/** Open weft-ui (the expert surface, same workspace) in a new window. */
async function openAdvanced(site?: string) {
  try {
    const r = await computeApi.advanced(site)
    if (r.available && r.url) {
      window.open(withBasePath(r.url), 'aba-weft-ui', 'width=1280,height=840')
    }
  } catch { /* unavailable — button is hidden anyway */ }
}

const fmtGB = (b?: number) => b == null ? '?' : `${(b / 1e9).toFixed(b >= 1e10 ? 0 : 1)} GB`

/** Cluster totals when partitions exist — never the login node's own cpus. */
export function capsLine(s: ComputeSite): string {
  const caps = s.capabilities
  const parts = caps?.scheduler?.partitions as
    | { nodes?: number; cpus_per_node?: number; gres?: { type?: string; count?: number }[] }[]
    | undefined
  if (parts?.length) {
    const nodes = parts.reduce((a, p) => a + (p.nodes ?? 0), 0)
    const cores = parts.reduce((a, p) => a + (p.nodes ?? 0) * (p.cpus_per_node ?? 0), 0)
    const gpus = parts.reduce((a, p) => a + (p.nodes ?? 0) *
      (p.gres ?? []).reduce((g, e) => g + (e.type === 'gpu' ? (e.count ?? 0) : 0), 0), 0)
    const bits = [`${nodes} nodes`, `${cores.toLocaleString()} cores`]
    if (gpus > 0) bits.push(`${gpus}× GPU`)
    return bits.join(' · ')
  }
  const bits: string[] = []
  if (caps?.cpus) bits.push(`${caps.cpus} cores`)
  if (caps?.mem_gb) bits.push(`${caps.mem_gb} GB`)
  for (const g of caps?.gpus ?? []) bits.push(`${g.count ?? 1}× ${g.model ?? 'GPU'}`)
  return bits.join(' · ')
}

const KIND_LABEL: Record<string, string> = {
  local: 'this machine', ssh: 'remote server', slurm: 'Slurm cluster',
}

function useForLine(s: ComputeSite): string {
  const uf = s.aba?.use_for ?? []
  const words: string[] = []
  if (uf.includes('interactive')) words.push('interactive analysis')
  if (uf.includes('background')) words.push('background jobs')
  if (uf.includes('gpu')) words.push('GPU work')
  return words.join(' · ') || 'everyday analyses'
}

function healthChip(s: ComputeSite): { text: string; cls: string } {
  if (s.verify?.state === 'running') return { text: '⏳ Verifying…', cls: 'is-busy' }
  switch (s.health) {
    case 'ok': return { text: '✓ Ready', cls: 'is-ok' }
    case 'unreachable': return { text: '○ Unreachable', cls: 'is-bad' }
    case 'unknown': return { text: '◐ Checking…', cls: 'is-busy' }
    default: return { text: s.health ?? '—', cls: 'is-off' }
  }
}

export default function ComputeTab() {
  const [sites, setSites] = useState<ComputeSite[] | null>(null)
  const [status, setStatus] = useState<{ ok: boolean; detail: string } | null>(null)
  const [sel, setSel] = useState<string | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [advanced, setAdvanced] = useState(false)

  useEffect(() => {
    computeApi.advanced().then(r => setAdvanced(r.available)).catch(() => {})
  }, [])

  const load = useCallback(async () => {
    try {
      const st = await computeApi.status()
      setStatus(st)
      if (st.ok) setSites((await computeApi.sites()).sites)
    } catch { /* transient — next poll/event retries */ }
  }, [])

  useEffect(() => {
    load()
    const onEv = () => load()
    window.addEventListener('aba:compute', onEv)
    return () => window.removeEventListener('aba:compute', onEv)
  }, [load])

  // gentle poll while anything is verifying or still probing
  useEffect(() => {
    if (!sites?.some(s => s.verify?.state === 'running' || s.health === 'unknown')) return
    const t = setTimeout(load, 4000)
    return () => clearTimeout(t)
  }, [sites, load])

  if (status && !status.ok) {
    return (
      <section className="settings__section">
        <h3 className="settings__section-title">Compute</h3>
        <div className="cmp-offline">
          Compute substrate is offline — {status.detail}
        </div>
      </section>
    )
  }
  if (!sites) return <div className="settings__empty">Loading…</div>

  return (
    <section className="settings__section">
      <div className="cmp-head">
        <h3 className="settings__section-title">Compute</h3>
        <div className="cmp-actions">
          <button className="cmp-btn cmp-btn--primary"
            onClick={() => setConnecting(true)}>+ Add a machine</button>
          {advanced && (
            <button className="cmp-btn" title="Open weft-ui — every knob exposed"
              onClick={() => openAdvanced()}>Advanced ↗</button>
          )}
        </div>
      </div>
      <p className="settings__hint">
        Where your analyses run. Add your lab’s cluster or a workstation and
        aba can run large jobs, GPU work, and overnight tasks there.
      </p>

      {connecting && (
        <ConnectMachine
          knownNames={sites.map(s => s.name)}
          onDone={() => { setConnecting(false); load() }}
          onCancel={() => setConnecting(false)} />
      )}

      <ul className="mod-list">
        {sites.map(s => (
          <SiteCard key={s.name} site={s} open={sel === s.name} advanced={advanced}
            onToggle={() => setSel(sel === s.name ? null : s.name)}
            onChanged={load} />
        ))}
      </ul>

      {sites.length <= 1 && !connecting && (
        <p className="cmp-pitch">
          Big analyses currently run on this machine only. Connecting a cluster
          takes about two minutes if you already ssh into it.
        </p>
      )}
    </section>
  )
}

// ── one machine: card + expandable manage detail (§6) ────────────────────────

function SiteCard({ site, open, advanced, onToggle, onChanged }: {
  site: ComputeSite; open: boolean; advanced: boolean
  onToggle: () => void; onChanged: () => void
}) {
  const s = site
  const isLocal = s.name === 'local'
  const chip = healthChip(s)
  const caps = capsLine(s)
  const contractLine =
    s.aba?.contract === 'shared-fs' && !isLocal
      ? 'sees your files directly (shared filesystem)'
      : s.aba?.contract === 'detached' ? 'ships work over (no shared files)' : null

  return (
    <li className="mod-card">
      <div className="mod-card__head cmp-card__head" onClick={onToggle}
        role="button" aria-expanded={open}>
        <span className="mod-card__title">{isLocal ? 'This machine' : s.name}</span>
        <span className="mod-card__meta">
          {useForLine(s)}{caps ? ` · ${caps}` : ''} · {KIND_LABEL[s.kind] ?? s.kind}
        </span>
        <span className={`mod-chip ${chip.cls}`}>{chip.text}</span>
      </div>
      {(contractLine || s.verify) && (
        <p className="mod-card__desc">
          {contractLine}
          {s.verify?.state === 'done' && (
            <span className={s.verify.ok ? 'cmp-verify-ok' : 'cmp-verify-bad'}>
              {contractLine ? ' · ' : ''}
              {s.verify.ok
                ? 'test job ran on every queue'
                : `queue verification failed: ${(s.verify.failed ?? []).join(', ') || s.verify.error?.detail || 'see Advanced'}`}
            </span>
          )}
        </p>
      )}
      {open && <SiteDetail site={s} advanced={advanced} onChanged={onChanged} />}
    </li>
  )
}

const USE_FOR_ALL: { key: string; label: string }[] = [
  { key: 'interactive', label: 'interactive analysis' },
  { key: 'background', label: 'background jobs' },
  { key: 'gpu', label: 'GPU work' },
]

function SiteDetail({ site, advanced, onChanged }: {
  site: ComputeSite; advanced: boolean; onChanged: () => void
}) {
  const s = site
  const isLocal = s.name === 'local'
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [estimate, setEstimate] = useState<string | null>(null)
  const [footprint, setFootprint] = useState<number | null>(null)
  const [reclaim, setReclaim] = useState<number | null>(null)
  const [longTerm, setLongTerm] = useState<StoragePath[]>(s.aba?.storage ?? [])
  const [newPath, setNewPath] = useState('')
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)
  const [editingRoot, setEditingRoot] = useState<string | null>(null)
  const [notesText, setNotesText] = useState(
    (s.config?.policy?.notes ?? []).join('\n'))

  const isScheduler = (s.capabilities?.scheduler?.type ?? 'none') !== 'none'

  // lazy, on expand only: start estimate (scheduler sites) + aba's footprint
  useEffect(() => {
    let stop = false
    if (isScheduler) {
      computeApi.load(s.name).then(r => {
        if (!stop && r.start_estimate) setEstimate(String(r.start_estimate))
      }).catch(() => {})
    }
    computeApi.footprint(s.name).then(r => {
      if (!stop && r.prefixes_bytes != null) {
        setFootprint((r.prefixes_bytes ?? 0) + (r.package_cache_bytes ?? 0))
      }
    }).catch(() => {})
    return () => { stop = true }
  }, [s.name, isScheduler])

  async function act(label: string, fn: () => Promise<unknown>, done?: string) {
    setBusy(label); setNote(null)
    try { await fn(); if (done) setNote(done); onChanged() }
    catch (e) { setNote(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(null) }
  }

  const caps = s.capabilities
  const useFor = s.aba?.use_for ?? []
  const toggleUseFor = (key: string) => {
    const next = useFor.includes(key) ? useFor.filter(u => u !== key) : [...useFor, key]
    act('usefor', () => computeApi.edit(s.name, { use_for: next }))
  }

  const saveLongTerm = (next: StoragePath[]) => {
    setLongTerm(next)
    act('storage', () => computeApi.edit(s.name, { long_term: next }))
  }

  return (
    <div className="cmp-detail">
      {caps && (
        <dl className="cmp-facts">
          {estimate && <><dt>a 4-core job would start</dt><dd>{estimate}</dd></>}
          <dt>hardware</dt>
          <dd>{capsLine(s) || '—'}{caps.arch ? ` · ${caps.arch}` : ''}</dd>
          <dt>internet on node</dt>
          <dd>{caps.internet ? 'yes' : 'no — packages arrive pre-packed'}</dd>
          {caps.scheduler?.type && caps.scheduler.type !== 'none' && (
            <><dt>scheduler</dt>
              <dd>{caps.scheduler.type}{caps.scheduler.version ? ` v${caps.scheduler.version}` : ''}</dd></>
          )}
          {s.config?.root && (
            <><dt>working space</dt>
              <dd>
                {editingRoot === null ? (
                  <>
                    {s.config.root} <span className="cmp-dim">· environments &amp;
                    working files — rebuilds itself</span>{' '}
                    {!isLocal && (
                      <button className="mod-linkbtn" disabled={busy !== null}
                        onClick={() => setEditingRoot(s.config?.root ?? '')}>change…</button>
                    )}
                  </>
                ) : (
                  <span className="cmp-addpath">
                    <input value={editingRoot} spellCheck={false} autoComplete="off"
                      onChange={e => setEditingRoot(e.target.value)} />
                    <button className="cmp-btn" disabled={busy !== null || !editingRoot.trim()}
                      onClick={() => act('root', async () => {
                        await computeApi.edit(s.name, { working_root: editingRoot.trim() })
                        setEditingRoot(null)
                      }, 'moved — environments rebuild there; the old location is not cleaned up (Free up first if needed)')}>Save</button>
                    <button className="mod-linkbtn" onClick={() => setEditingRoot(null)}>cancel</button>
                  </span>
                )}
              </dd></>
          )}
        </dl>
      )}

      <div className="cmp-block">
        <div className="cmp-block__title">Used for</div>
        <div className="cmp-chips">
          {USE_FOR_ALL.map(u => (
            <button key={u.key} disabled={isLocal || busy !== null}
              className={`cmp-chip ${useFor.includes(u.key) ? 'is-on' : ''}`}
              aria-pressed={useFor.includes(u.key)}
              onClick={() => !isLocal && toggleUseFor(u.key)}>{u.label}</button>
          ))}
        </div>
      </div>

      {!isLocal && (
        <div className="cmp-block">
          <div className="cmp-block__title">
            Long-term store <span className="cmp-dim">— data read in place; results
            kept here stay put</span>
          </div>
          {longTerm.length === 0 && <div className="cmp-dim">none declared</div>}
          <ul className="cmp-paths">
            {longTerm.map((p, i) => (
              <li key={p.path}>
                <code>{p.path}</code>
                <button className="mod-linkbtn" disabled={busy !== null}
                  onClick={() => saveLongTerm(longTerm.filter((_, j) => j !== i))}>remove</button>
              </li>
            ))}
          </ul>
          <div className="cmp-addpath">
            <input value={newPath} placeholder="add a path…" spellCheck={false}
              autoComplete="off"
              onChange={e => setNewPath(e.target.value)} />
            <button className="cmp-btn" disabled={!newPath.trim() || busy !== null}
              onClick={() => {
                saveLongTerm([...longTerm, { path: newPath.trim(), stable: true }])
                setNewPath('')
              }}>Add path</button>
          </div>
        </div>
      )}

      {!isLocal && (
        <div className="cmp-block">
          <div className="cmp-block__title">
            Notes <span className="cmp-dim">— guidance for scheduling; the agent
            sees this with every plan</span>
          </div>
          <textarea className="cmp-notes" rows={2} spellCheck={false}
            placeholder={'e.g. "use only on nights, EU time"'}
            value={notesText}
            onChange={e => setNotesText(e.target.value)} />
          {notesText !== (s.config?.policy?.notes ?? []).join('\n') && (
            <button className="cmp-btn" disabled={busy !== null}
              onClick={() => act('notes', () =>
                computeApi.edit(s.name, { notes: notesText.split('\n') }),
                'notes saved')}>Save notes</button>
          )}
        </div>
      )}

      {footprint != null && footprint > 0 && (
        <div className="cmp-block">
          <div className="cmp-block__title">Disk</div>
          <span>aba is using {fmtGB(footprint)} on this machine</span>{' '}
          {reclaim == null ? (
            <button className="mod-linkbtn" disabled={busy !== null}
              onClick={() => act('gc', async () => {
                const r = await computeApi.gc(s.name, false)
                setReclaim(r.reclaimable_bytes ?? 0)
              })}>Free up…</button>
          ) : (
            <span>
              {fmtGB(reclaim)} reclaimable{' '}
              <button className="mod-linkbtn" disabled={busy !== null || reclaim === 0}
                onClick={() => act('gc', () => computeApi.gc(s.name, true),
                  'space reclaimed')}>Reclaim now</button>
              <button className="mod-linkbtn" onClick={() => setReclaim(null)}>cancel</button>
            </span>
          )}
        </div>
      )}

      <div className="cmp-actions">
        {!isLocal && (
          <button className="cmp-btn" disabled={busy !== null}
            onClick={() => act('test', () => computeApi.reprobe(s.name),
              'connection ok — capabilities refreshed')}>Test connection</button>
        )}
        {isScheduler && (
          <button className="cmp-btn" disabled={busy !== null || s.verify?.state === 'running'}
            onClick={() => act('verify', () => computeApi.verify(s.name),
              'running a small test job on each queue…')}>Verify queues</button>
        )}
        {!isLocal && !confirmDisconnect && (
          <button className="cmp-btn cmp-btn--danger" disabled={busy !== null}
            onClick={() => setConfirmDisconnect(true)}>Disconnect…</button>
        )}
        {advanced && (
          <button className="cmp-btn" title="This machine in weft-ui — every knob exposed"
            onClick={() => openAdvanced(s.name)}>Advanced ↗</button>
        )}
      </div>
      {confirmDisconnect && (
        <div className="cmp-confirm">
          aba will forget this machine. Nothing on the machine is deleted — aba’s
          files there{footprint != null && footprint > 0 ? ` (${fmtGB(footprint)})` : ''} stay.
          <div className="cmp-actions">
            <button className="cmp-btn cmp-btn--danger" disabled={busy !== null}
              onClick={() => act('disconnect', () => computeApi.disconnect(s.name))}>
              Disconnect
            </button>
            <button className="cmp-btn" onClick={() => setConfirmDisconnect(false)}>Keep it</button>
          </div>
        </div>
      )}
      {note && <div className="cmp-note">{note}</div>}
    </div>
  )
}
