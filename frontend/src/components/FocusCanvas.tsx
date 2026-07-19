import { useEffect, useState } from 'react'
import type { Entity } from '../types'
import ProvenanceSection from './ProvenanceSection'
import PromoteDialog from './PromoteDialog'
import AnnotatedFigure from '../bio/AnnotatedFigure'
import ThreadHeader from './ThreadHeader'
import SplitButton from './SplitButton'
// Importing the bio side has the side-effect of registering all bio
// focus-view components against the registry. The shell below dispatches
// via `focus_view_for` — it never references entity-type-specific
// components directly, so adding a new bio type needs no shell edit.
import { focus_view_for } from '../bio/focusViews'
import './FocusCanvas.css'

interface Annotation { image: string; note: string }

interface Props {
  entity: Entity | null
  entities: Entity[]
  onChange: () => void
  onFocus: (id: string) => void
  onSelectThread?: (id: string) => void
  onAnnotate?: (a: Annotation) => void
  annotClear?: number
  /** Compact peek variant (chat-first): trim meta + provenance for the rail. */
  compact?: boolean
  /** Hand a request to the Guide (e.g. a Run's Re-run / Discuss → chat). */
  onAsk?: (text: string) => void
  /** Seed the Guide composer WITHOUT sending (reveals the chat peek, focuses
   *  the cursor) — the Discuss-style hook: user confirms with Enter. */
  onPrefill?: (text: string) => void
  /** "Chat" gesture on a run output / focus surface — brings the plot (with
   *  its image) into chat. The optional `action` parameter (Stage 5 of
   *  misc/exec_records_and_versioning.md) switches the composer prefill
   *  between chat / make-a-revision / reproduce so the same callback
   *  drives all three SplitButton dropdown options. */
  onChatResult?: (label: string, thumb?: string,
                  annotation?: { image: string; note: string },
                  action?: 'chat' | 'revision' | 'revision-supersede' | 'reproduce',
                  entityId?: string) => void
  /** Run view → switch the left rail to the Files tab, deep-linking to a folder. */
  onBrowseFiles?: (path?: string) => void
  /** Per-request project pin for upload routing (dataset "Add files"). */
  projectId?: string
  /** Highlight-mode toggle, lifted from App.tsx so the canvas-actions row's
   *  ✏️ button drives both ChatPane AND result-focused MemberPanels. */
  highlighting?: boolean
  onHighlightingChange?: (on: boolean) => void
}

type PromoteMode =
  | { kind: 'figure-to-claim' }
  | { kind: 'scenario' }

export default function FocusCanvas({ entity, entities, onChange, onFocus, onSelectThread, onAnnotate, annotClear, compact, onAsk, onPrefill, onChatResult, onBrowseFiles, projectId, highlighting, onHighlightingChange }: Props) {
  const [promote, setPromote] = useState<PromoteMode | null>(null)
  const [compareOn, setCompareOn] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)

  if (!entity || entity.type === 'workspace') {
    return <WorkspaceCanvas entities={entities} onChange={onChange} onFocus={onFocus} />
  }

  // Create a claim from this result (figure/table), citing it as evidence. In
  // entity-model v3 figures/tables ARE results, and the gesture is figure→Claim
  // directly (the old figure→result→finding→claim chain is gone).
  async function doFigureClaim(text: string) {
    const tid = (entity!.metadata?.thread_id as string) || 'default'
    const r = await fetch('/api/claims', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ statement: text, evidence_ids: [entity!.id], thread_id: tid }),
    })
    if (!r.ok) throw new Error(`claim failed: ${r.status} ${await r.text()}`)
    const created: Entity = await r.json()
    setPromote(null)
    onChange()
    onFocus(created.id)
  }

  // Start a Result (kept observation) seeded with this figure/table; opens it so
  // the user can add a reading, more panels, or notes. Deliberate grouping —
  // never the pin gesture.
  async function groupIntoResult() {
    const e = entity!
    const tid = (e.metadata?.thread_id as string) || 'default'
    const r = await fetch('/api/results', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        thread_id: tid, title: e.title,
        interpretation: (e.metadata?.interpretation as string) || '',
        members: [{ kind: e.type === 'table' ? 'table' : 'figure', ref: e.id }],
      }),
    })
    if (!r.ok) return
    const created: Entity = await r.json()
    onChange()
    onFocus(created.id)
  }

  // Resolve baseline for compare view (when focused on a scenario variant).
  const baseline = entity.scenario_of
    ? entities.find(e => e.id === entity.scenario_of) ?? null
    : null

  return (
    <div className={`focus ${compact ? 'focus--compact' : ''}`}>
      {/* Runs and Results render their own title header; skip the generic one
          to avoid a duplicate type pill + title. */}
      {entity.type !== 'analysis' && entity.type !== 'result' && (
      <div className="focus__header">
        <span className={`focus__type focus__type--${entity.type}`}>{entity.type}</span>
        <h2 className="focus__title">{entity.title}</h2>
        {entity.metadata?.by_reference ? (
          <span
            className="focus__ref-badge"
            title={`Imported by reference — the payload lives at ${String(entity.metadata?.ref_path ?? '')} and is not copied into ABA.`}
          >
            ↪ external
          </span>
        ) : null}
        {entity.scenario_of && baseline && (
          <span className="focus__scenario-badge" title={`scenario of ${baseline.title}`}>
            scenario of <em>{baseline.title}</em>
          </span>
        )}
        {baseline && (
          <button
            className={`focus__compare ${compareOn ? 'focus__compare--on' : ''}`}
            onClick={() => setCompareOn(v => !v)}
            title="Compare scenario against its baseline"
          >
            ⇆ Compare
          </button>
        )}
        {entity.type === 'figure' && (
          <button
            className={`focus__clock ${historyOpen ? 'focus__clock--on' : ''}`}
            onClick={() => setHistoryOpen(v => !v)}
            title="Version history"
          >
            <svg width="15" height="15" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10 2a8 8 0 100 16 8 8 0 000-16zm0 14a6 6 0 110-12 6 6 0 010 12zm.5-9H9v4l3.2 1.9.8-1.3-2.5-1.5V7z"/>
            </svg>
          </button>
        )}
        {renderActionButton(entity, setPromote, onChatResult, groupIntoResult)}
      </div>
      )}
      {historyOpen && entity.type === 'figure' && (
        <HistoryDrawer entity={entity} onFocus={onFocus} onClose={() => setHistoryOpen(false)} />
      )}
      <div className="focus__body">
        {/* Body dispatch:
            - thread: shell-level header (not a bio entity-type view)
            - compare mode on a figure scenario: side-by-side baseline panel
            - annotation mode on a figure: AnnotatedFigure handles the
              click-to-region overlay; ordinary figures fall through to
              the registry
            - everything else: registry lookup via focus_view_for(type).
              An unregistered type falls back to a generic placeholder so
              a YAML without a registered view doesn't white-screen. */}
        {entity.type === 'thread'
          ? <ThreadHeader thread={entity} full onChange={onChange} onSwitchThread={onSelectThread ?? onFocus} />
          : compareOn && baseline && entity.type === 'figure'
          ? renderCompareBody(entity, baseline)
          : entity.type === 'figure' && onAnnotate
          ? <AnnotatedFigure entity={entity} onAttach={onAnnotate} clearSignal={annotClear} />
          : renderRegistryView(entity, entities, onFocus, onChange, compact, onAsk, onChatResult, onBrowseFiles, projectId, onAnnotate, annotClear, highlighting, onHighlightingChange, onPrefill)}
      </div>
      <div className="focus__meta">
        <span title={entity.id}>id {entity.id}</span>
        <span>•</span>
        <span>created {new Date(entity.created_at).toLocaleString()}</span>
        {!compact && entity.parent_entity_id && (
          <>
            <span>•</span>
            <span>parent {entity.parent_entity_id}</span>
          </>
        )}
      </div>

      {!compact && <ProvenanceSection entity={entity} onFocus={onFocus} />}

      {promote?.kind === 'figure-to-claim' && (
        <PromoteDialog
          title={`Create a claim from "${entity.title}"`}
          prompt="State the claim this result supports — keep it sharp, it can be challenged later. (Pre-filled with Guide's read — edit freely.)"
          placeholder="Proneural-marker-high cells form a stable subpopulation, not a transitional state."
          suggest={async () => {
            const r = await fetch(`/api/entities/${encodeURIComponent(entity.id)}/suggest-interpretation`)
            return r.ok ? (await r.json()).text ?? '' : ''
          }}
          onCancel={() => setPromote(null)}
          onSubmit={doFigureClaim}
        />
      )}
    </div>
  )
}

function WorkspaceCanvas({
  entities, onChange, onFocus,
}: {
  entities: Entity[]
  onChange: () => void
  onFocus: (id: string) => void
}) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const datasetCount = entities.filter(e => e.type === 'dataset').length

  async function uploadFile(f: File) {
    setBusy(true); setErr(null)
    try {
      const form = new FormData()
      form.append('file', f)
      const r = await fetch('/api/upload', { method: 'POST', body: form })
      if (!r.ok) throw new Error(await r.text())
      const created: Entity = await r.json()
      onChange(); onFocus(created.id)
    } catch (e) { setErr(String(e)) }
    finally { setBusy(false) }
  }

  async function submitUrl() {
    if (!url.trim() || busy) return
    setBusy(true); setErr(null)
    try {
      const r = await fetch('/api/upload-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim() }),
      })
      if (!r.ok) throw new Error(await r.text())
      const created: Entity = await r.json()
      onChange(); onFocus(created.id); setUrl('')
    } catch (e) { setErr(String(e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="focus focus--workspace">
      <h2 className="focus__title">Workspace</h2>
      <p className="focus__empty-sub">
        Drop a CSV or paste a URL to add data. Once you have something to work
        with, you can talk to Guide below.
      </p>
      <div className="workspace__add">
        <label className="workspace__file-btn" htmlFor="ws-upload">
          Upload file…
          <input
            id="ws-upload"
            type="file"
            style={{ display: 'none' }}
            onChange={e => {
              const f = e.target.files?.[0]
              if (f) uploadFile(f)
            }}
            disabled={busy}
          />
        </label>
        <span className="workspace__or">or</span>
        <input
          className="workspace__url"
          type="url"
          placeholder="paste a URL…"
          value={url}
          onChange={e => setUrl(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') submitUrl() }}
          disabled={busy}
        />
        <button
          className="workspace__url-btn"
          onClick={submitUrl}
          disabled={busy || !url.trim()}
        >
          {busy ? 'Downloading…' : 'Add'}
        </button>
      </div>
      {err && <div className="workspace__err">{err}</div>}
      <div className="workspace__hint">
        {datasetCount === 0
          ? 'no data uploaded yet.'
          : `${datasetCount} dataset${datasetCount === 1 ? '' : 's'} in this project.`}
      </div>
      <div className="workspace__add" style={{ marginTop: 6 }}>
        <button
          className="workspace__file-btn"
          disabled={busy}
          onClick={async () => {
            setBusy(true)
            try {
              const r = await fetch('/api/narratives', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: 'New manuscript section', text: '' }),
              })
              if (r.ok) { const n = await r.json(); onChange(); onFocus(n.id) }
            } finally { setBusy(false) }
          }}
        >
          + New manuscript section
        </button>
      </div>
    </div>
  )
}

function renderCompareBody(scenario: Entity, baseline: Entity) {
  return (
    <div className="focus__compare-grid">
      <div className="focus__compare-pane">
        <div className="focus__compare-label">baseline</div>
        <h3 className="focus__compare-title">{baseline.title}</h3>
        {baseline.artifact_path && (
          <img className="focus__figure" src={baseline.artifact_path} alt={baseline.title} />
        )}
      </div>
      <div className="focus__compare-pane">
        <div className="focus__compare-label">scenario</div>
        <h3 className="focus__compare-title">{scenario.title}</h3>
        {scenario.artifact_path && (
          <img className="focus__figure" src={scenario.artifact_path} alt={scenario.title} />
        )}
        {scenario.metadata?.scenario_description != null && (
          <div className="focus__compare-desc">
            change: {String(scenario.metadata.scenario_description)}
          </div>
        )}
      </div>
    </div>
  )
}

function renderActionButton(
  entity: Entity,
  setPromote: (m: PromoteMode | null) => void,
  onChatResult?: (label: string, thumb?: string,
                  annotation?: { image: string; note: string },
                  action?: 'chat' | 'revision' | 'revision-supersede' | 'reproduce',
                  entityId?: string) => void,
  onGroup?: () => void,
) {
  // Figures/tables → group into a Result (deliberate), and draft a claim.
  if (entity.type === 'figure' || entity.type === 'table' || entity.type === 'result') {
    const can_chat = onChatResult != null
      && (entity.type === 'figure' || entity.type === 'table')
    return (
      <div className="focus__actions">
        {can_chat && (
          /* Stage 5: SplitButton default is "Chat about this figure"; the
             dropdown surfaces "Make a revision" + "Reproduce", both of
             which prefill the composer with a tailored instruction so
             the agent uses the make_revision / reproduce_from_exec
             tools (see misc/exec_records_and_versioning.md). */
          <SplitButton
            primary={{
              label: `💬 Chat about this ${entity.type}`,
              title: `Bring "${entity.title}" into chat`,
              onClick: () => onChatResult!(entity.title,
                                            entity.artifact_path ?? undefined,
                                            undefined, 'chat', entity.id),
            }}
            options={[
              {
                label: `Chat about this ${entity.type}`,
                description: 'Bring it into the composer with the image attached',
                emphasis: true,
                onClick: () => onChatResult!(entity.title,
                                              entity.artifact_path ?? undefined,
                                              undefined, 'chat', entity.id),
              },
              {
                label: 'Make a revision',
                description: 'Re-run the producing code with a change; pinned as a sibling',
                onClick: () => onChatResult!(entity.title,
                                              entity.artifact_path ?? undefined,
                                              undefined, 'revision', entity.id),
              },
              {
                label: 'Reproduce',
                description: 'Re-run the exec; flag any env drift',
                onClick: () => onChatResult!(entity.title,
                                              entity.artifact_path ?? undefined,
                                              undefined, 'reproduce', entity.id),
              },
            ]}
          />
        )}
        {(entity.type === 'figure' || entity.type === 'table') && onGroup && (
          <button
            className="focus__promote"
            onClick={onGroup}
            title="Start a Result (observation) from this — then add a reading, more panels, or notes"
          >
            ⊞ Group into a result
          </button>
        )}
        <button
          className="focus__promote focus__promote--claim"
          onClick={() => setPromote({ kind: 'figure-to-claim' })}
          title="Draft a claim supported by this result"
        >
          ✦ Create a claim
        </button>
      </div>
    )
  }
  return null
}

/**
 * Render the entity-aware body via the bio focus-view registry.
 * The shell never references entity-type-specific components — adding
 * a new bio type means a YAML + a `register_focus_view` call, not a
 * FocusCanvas edit.
 *
 * An unregistered type falls back to a generic placeholder so a YAML
 * that lacks a view doesn't white-screen (Phase 4.5's "warn, don't
 * crash" stance carried into the shell).
 */
function renderRegistryView(
  e: Entity,
  entities: Entity[],
  onFocus: (id: string) => void,
  onChange: () => void,
  compact?: boolean,
  onAsk?: (t: string) => void,
  onChatResult?: (label: string, thumb?: string,
                  annotation?: { image: string; note: string },
                  action?: 'chat' | 'revision' | 'revision-supersede' | 'reproduce',
                  entityId?: string) => void,
  onBrowseFiles?: (path?: string) => void,
  projectId?: string,
  onAnnotate?: (a: { image: string; note: string }) => void,
  annotClear?: number,
  highlighting?: boolean,
  onHighlightingChange?: (on: boolean) => void,
  onPrefill?: (text: string) => void,
) {
  const View = focus_view_for(e.type)
  if (!View) {
    return <p className="focus__placeholder">Detail view not yet implemented for &lsquo;{e.type}&rsquo;.</p>
  }
  return <View
    entity={e}
    entities={entities}
    onFocus={onFocus}
    onChange={onChange}
    compact={compact}
    onAsk={onAsk}
    onPrefill={onPrefill}
    onChatResult={onChatResult}
    onBrowseFiles={onBrowseFiles}
    projectId={projectId}
    onAnnotate={onAnnotate}
    annotClear={annotClear}
    highlighting={highlighting}
    onHighlightingChange={onHighlightingChange}
  />
}

function HistoryDrawer({
  entity, onFocus, onClose,
}: {
  entity: Entity; onFocus: (id: string) => void; onClose: () => void
}) {
  const [versions, setVersions] = useState<Entity[]>([])
  useEffect(() => {
    let cancelled = false
    fetch(`/api/entities/${encodeURIComponent(entity.id)}/history`)
      .then(r => (r.ok ? r.json() : Promise.reject(r)))
      .then((v: Entity[]) => { if (!cancelled) setVersions(v) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [entity.id])

  if (versions.length <= 1) {
    return (
      <div className="history">
        <div className="history__head">Version history <button className="history__close" onClick={onClose}>×</button></div>
        <div className="history__empty">This is the only version so far.</div>
      </div>
    )
  }

  return (
    <div className="history">
      <div className="history__head">
        {versions.length} versions
        <button className="history__close" onClick={onClose}>×</button>
      </div>
      <div className="history__strip">
        {versions.map((v, i) => (
          <button
            key={v.id}
            className={`history__thumb ${v.id === entity.id ? 'history__thumb--current' : ''}`}
            onClick={() => onFocus(v.id)}
            title={new Date(v.created_at).toLocaleString()}
          >
            {v.artifact_path && <img src={v.artifact_path} alt={v.title} />}
            <div className="history__thumb-label">
              {i === 0 ? 'current' : `v${versions.length - i}`}
              <span className="history__thumb-date">
                {new Date(v.created_at).toLocaleDateString()}
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}


