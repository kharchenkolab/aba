/**
 * ProvenanceSection — the "how this was made" affordance for a single entity.
 * Unobtrusive when idle (one muted line naming the primary input + method),
 * informative on dive-in (a label→value grid: Inputs / Method / Environment /
 * By·when / Lineage). Fed by GET /api/entities/{id}/provenance.
 *
 * Used in two places:
 *  - the card footer (FocusCanvas) for the focused entity, and
 *  - per-PANEL inside a Result (each figure/table carries its own provenance;
 *    a Result itself is a curation, so its footer is curation+lineage only).
 *
 * Expanding never changes focus — only clicking an input/lineage chip navigates
 * (explicit). So a per-panel section can't hijack what the agent thinks you're
 * looking at.
 */
import { useEffect, useState } from 'react'
import type { Entity } from '../types'
import { getEntityProvenance } from '../lib/api'
import type { EntityProvenance, ProvNode, ProvInput } from '../lib/api'
import './ProvenanceSection.css'

export function fmtActor(a?: string | null): string {
  if (!a) return ''
  if (a === 'system') return 'the system'
  if (a === 'legacy') return 'unrecorded'
  if (a.startsWith('human:')) return 'you'        // single-user: human:local; real uid arrives with identity
  if (a.startsWith('agent:')) return 'an agent'
  return a
}

export function fmtDerivation(d?: Entity['derivation']): string {
  if (!d) return ''
  const n = d.sources?.length ?? 0
  switch (d.kind) {
    case 'exec': return 'computed'
    case 'derived_from': return `derived from ${n} source${n === 1 ? '' : 's'}`
    case 'imported': return d.source ? `imported (${d.source})` : 'imported'
    case 'manual': return 'created here'
    case 'legacy': return 'origin unrecorded'
    default: return d.kind
  }
}

function fmtProvDate(iso?: string | null): string {
  if (!iso) return ''
  try { return new Date(iso).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }
  catch { return iso }
}

function fmtDuration(s?: number | null): string {
  if (s == null) return ''
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)}s`
  const m = Math.floor(s / 60), sec = Math.round(s % 60)
  return `${m}m${sec ? ` ${sec}s` : ''}`
}

// A directional lineage arrow: upstream = "made from", downstream = "used by".
function LineageChip({ n, dir, onFocus }: { n: ProvNode; dir: 'up' | 'down'; onFocus: (id: string) => void }) {
  return (
    <button className="prov-lin" onClick={() => onFocus(n.id)} title={`${n.rel} · ${n.id}`}>
      <span className="prov-lin__arrow">{dir === 'up' ? '←' : '→'}</span>
      <span className={`focus__type focus__type--${n.type}`}>{n.type}</span>
      <span className="prov-lin__title">{n.title}</span>
      {n.rel && <span className="prov-lin__rel">{n.rel === 'used' && dir === 'up' ? 'input' : n.rel}</span>}
    </button>
  )
}

// An input the run USED (dataset / file / reference), with its version on hover.
function InputChip({ inp, onFocus }: { inp: ProvInput; onFocus: (id: string) => void }) {
  const clickable = inp.exists !== false && !!inp.ref && inp.kind !== 'file'
  const label = inp.name || inp.title || inp.ref
  const tip = [inp.path, inp.version && `version ${inp.version}`, inp.ref].filter(Boolean).join('\n')
  return (
    <button className="prov-chip" disabled={!clickable} title={tip}
            onClick={() => clickable && onFocus(inp.ref)}>
      <span className={`focus__type focus__type--${inp.kind}`}>{inp.kind}</span>
      <span className="prov-chip__label">{label}</span>
    </button>
  )
}

export default function ProvenanceSection({ entity, onFocus, label = 'Provenance', className = '' }: {
  entity: Entity
  onFocus: (id: string) => void
  /** Toggle label — "Provenance" for a card footer, "Source" per panel. */
  label?: string
  /** Extra class on the root (e.g. `prov--panel` for the compact per-panel variant). */
  className?: string
}) {
  const [data, setData] = useState<EntityProvenance | null>(null)
  const [open, setOpen] = useState(false)
  const [showCode, setShowCode] = useState(false)

  useEffect(() => {
    setData(null); setShowCode(false)
    if (entity.type === 'workspace') return
    let cancelled = false
    getEntityProvenance(entity.id)
      .then(d => { if (!cancelled) setData(d) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [entity.id, entity.type])

  if (entity.type === 'workspace') return null   // the root isn't a provenance-bearing entity

  const inputs = data?.inputs ?? []
  const method = data?.method ?? {}
  const env = data?.environment ?? {}
  const attr = data?.attribution ?? {}
  const up = data?.lineage?.upstream ?? data?.upstream ?? []
  const down = data?.lineage?.downstream ?? data?.downstream ?? []
  const originText = fmtDerivation(entity.derivation)
  const actorText = fmtActor(attr.actor ?? entity.actor)
  const originKind = entity.derivation?.kind
  // Origin worth its OWN row: imported (carries a source), created-here, or
  // unrecorded. 'exec' is redundant with Method; 'derived_from' is already the
  // Inputs/Lineage rows. (An imported dataset can also carry the registering
  // run's exec via exec_id — that shows as Method/Environment, so the source
  // needs this explicit row or it never appears.)
  const showOrigin = !!originText && originKind !== 'exec' && originKind !== 'derived_from'

  const hasMethod = !!(method.recipe_id || method.code || method.language || method.command)
  const hasEnv = !!(env.language_version || (env.key_packages && env.key_packages.length))
  const hasAny = inputs.length || hasMethod || hasEnv || up.length || down.length || originText || actorText
  if (!hasAny) return null

  // Collapsed summary — names the primary input + the method (or, for a curation
  // like a Result, who assembled it), so you can decide whether to dive in.
  const via = method.recipe_id
    || (method.language ? `${method.language}${env.language_version ? ' ' + env.language_version : ''}` : '')
  const summaryBits: string[] = []
  // An imported entity's headline IS its origin — lead with it so the source
  // shows even collapsed and the registering run's method can't mask it.
  if (originKind === 'imported' && originText) summaryBits.push(originText)
  if (inputs.length) {
    const first = inputs[0].name || inputs[0].title || inputs[0].kind
    summaryBits.push(`from ${first}${inputs.length > 1 ? ` +${inputs.length - 1}` : ''}`)
  }
  if (via) summaryBits.push(`via ${via}`)
  if (!summaryBits.length) {
    // No exec-born method/inputs (e.g. a Result): fall back to the curation act.
    if (!hasMethod && !inputs.length && actorText) summaryBits.push(`assembled by ${actorText}`)
    else if (originText) summaryBits.push(originText)
  }
  const summary = summaryBits.join('  ·  ')

  const keyPkgs = env.key_packages ?? []
  const extraPkgs = env.package_count ? Math.max(0, env.package_count - keyPkgs.length) : 0

  return (
    <div className={`prov ${className} ${open ? 'prov--open' : ''}`.trim()}>
      <button className="prov__toggle" onClick={() => setOpen(v => !v)}>
        <span className="prov__chev">{open ? '▾' : '▸'}</span>
        {label}
        {!open && summary && <span className="prov__summary">{summary}</span>}
        {env.drift && ((env.drift.changed ?? 0) > 0 || env.drift.moved) && (
          <span className="prov__badge prov__badge--warn"
                title={env.drift.changed
                  ? `The environment changed since this ran (${env.drift.changed} package${env.drift.changed === 1 ? '' : 's'})`
                  : 'The environment changed since this ran'}>
            ⚠ env drift
          </span>
        )}
      </button>
      {open && (
        <div className="prov__grid">
          {showOrigin && (
            <>
              <div className="prov__k">Origin</div>
              <div className="prov__v">{originText}</div>
            </>
          )}
          {inputs.length > 0 && (
            <>
              <div className="prov__k">Inputs</div>
              <div className="prov__v prov__chips">
                {inputs.map(i => <InputChip key={i.ref} inp={i} onFocus={onFocus} />)}
              </div>
            </>
          )}

          {hasMethod && (
            <>
              <div className="prov__k">Method</div>
              <div className="prov__v">
                {method.recipe_id && <span className="prov__recipe" title="recipe / pipeline">{method.recipe_id}</span>}
                {method.language && <span>{method.language}{env.language_version ? ` ${env.language_version}` : ''}</span>}
                {Array.isArray(method.command) && <code className="prov__cmd">{method.command.join(' ')}</code>}
                {method.steps ? <span className="prov__dim">· {method.steps} steps</span> : null}
                {method.code && (
                  <button className="prov__link" onClick={() => setShowCode(v => !v)}>
                    {showCode ? 'hide code' : 'show code'}
                  </button>
                )}
                {method.code_lines ? <span className="prov__dim">({method.code_lines} lines)</span> : null}
                {showCode && method.code && <pre className="prov__code">{method.code}</pre>}
              </div>
            </>
          )}

          {hasEnv && (
            <>
              <div className="prov__k">Environment</div>
              <div className="prov__v prov__env">
                {keyPkgs.map(p => <span key={p.name} className="prov__pkg">{p.name} {p.version}</span>)}
                {extraPkgs > 0 && <span className="prov__dim">+{extraPkgs} pkgs</span>}
                {env.images && env.images.length > 0 && (
                  <span className="prov__dim" title={env.images.join('\n')}>{env.images.length} container image{env.images.length === 1 ? '' : 's'}</span>
                )}
                {env.backfilled && <span className="prov__dim" title="Reconstructed from a legacy record — versions may be incomplete">legacy record</span>}
              </div>
            </>
          )}

          {(actorText || attr.completed_at || attr.seed != null) && (
            <>
              <div className="prov__k">By · when</div>
              <div className="prov__v prov__dim">
                {[actorText,
                  attr.completed_at ? fmtProvDate(attr.completed_at) : fmtProvDate(attr.created_at),
                  fmtDuration(attr.wall_time_s),
                  attr.seed != null ? `seed ${attr.seed}` : '',
                  attr.status && attr.status !== 'ok' ? attr.status : ''
                ].filter(Boolean).join('  ·  ')}
              </div>
            </>
          )}

          {(up.length > 0 || down.length > 0) && (
            <>
              <div className="prov__k">Lineage</div>
              <div className="prov__v prov__lineage">
                {up.map(n => <LineageChip key={'u' + n.id} n={n} dir="up" onFocus={onFocus} />)}
                {down.map(n => <LineageChip key={'d' + n.id} n={n} dir="down" onFocus={onFocus} />)}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
