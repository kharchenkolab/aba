/**
 * Thread overview — the thread-scoped analog of the Project overview (P8/P9).
 * Four columns: Pinned → Runs → Claims → Open Questions. Status-grouped,
 * filterable, click an entity to open it. Columns add intentionally (inline
 * input for claims/questions; a two-path dialog for pinned results); open
 * questions are click-to-edit with a "Discuss" action that asks the Guide.
 */
import { useState } from 'react'
import type { Entity } from '../types'
import { OverviewColumn, OverviewRow, RowMenu, InlineAdd, AddResourceDialog, type OvGroup, type Tone } from '../components/OverviewKit'
import EntityMenu from '../components/EntityMenu'
import './ProjectOverview.css'

interface OpenQ { id: string; text: string; status: string; promoted_to?: string }
type Filter = 'attention' | 'active' | 'contested' | 'all'

interface Props {
  entities: Entity[]
  thread: Entity
  threadId: string
  onGoTo: (id: string) => void
  onSelectThread: (id: string) => void
  onChange: () => void
  onAsk: (text: string) => void
}

const CONF_ORDER = ['validated', 'contested', 'supported', 'preliminary', 'refuted'] as const
const IMG = /\.(png|jpe?g|svg|webp|gif)$/i

export default function ThreadOverview({ entities, thread, threadId, onGoTo, onSelectThread, onChange, onAsk }: Props) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('active')
  const [editingOq, setEditingOq] = useState<string | null>(null)
  const [pinnedDialog, setPinnedDialog] = useState(false)
  const q = query.trim().toLowerCase()

  const byId = new Map(entities.map(e => [e.id, e]))
  const evidenceRefs = (e: Entity) => [
    ...((e.metadata?.evidence_ids as string[] | undefined) ?? []),
    ...((e.metadata?.supporting_findings as string[] | undefined) ?? []),
    ...((e.metadata?.evidence as string[] | undefined) ?? []),
    ...((e.metadata?.supporting_results as string[] | undefined) ?? []),
    ...(((e.metadata?.members as { ref?: string }[] | undefined) ?? []).map(m => m.ref).filter((ref): ref is string => !!ref)),
  ]
  const inThread = (e: Entity, seen = new Set<string>()): boolean => {
    if (e.metadata?.thread_id === threadId) return true
    if (seen.has(e.id)) return false
    seen.add(e.id)
    return evidenceRefs(e).some(id => {
      const ref = byId.get(id)
      return !!ref && inThread(ref, seen)
    })
  }
  const text = (e: Entity) => (e.metadata?.statement as string) || (e.metadata?.interpretation as string) || e.title
  const matches = (s: string) => !q || s.toLowerCase().includes(q)
  const passes = (e: Entity) => {
    if (filter === 'all') return true
    const conf = e.metadata?.confidence as string | undefined
    if (filter === 'active') return e.status !== 'archived' && e.status !== 'superseded' && conf !== 'refuted'
    if (filter === 'contested') return conf === 'contested'
    return conf === 'contested' || conf === 'refuted' || e.status === 'failed'  // attention
  }
  const keep = (e: Entity) => matches(text(e)) && passes(e)

  // A "pinned" item in the new model (post task #318) is an active Result —
  // the wrapper entity created when the user pins a figure/table. The
  // dropped `entity.pinned` flag is no longer the source of truth (it was
  // never populated, so the old filter returned empty).
  const pinned = entities.filter(e => e.type === 'result' && inThread(e) && e.status !== 'archived')
  const claims = entities.filter(e => e.type === 'claim' && inThread(e) && e.status !== 'archived')
  const runs = entities.filter(e => e.type === 'analysis' && inThread(e) && e.status !== 'archived'
    && !(e.metadata as { ambient?: boolean } | undefined)?.ambient)
  const oqs: OpenQ[] = ((thread.metadata?.open_questions as OpenQ[]) ?? [])

  const citedIds = new Set<string>()
  const addCited = (id: string, seen = new Set<string>()) => {
    if (seen.has(id)) return
    seen.add(id)
    citedIds.add(id)
    const ref = byId.get(id)
    if (ref) evidenceRefs(ref).forEach(child => addCited(child, seen))
  }
  for (const c of claims) for (const id of ((c.metadata?.evidence_ids as string[]) ?? [])) addCited(id)

  // --- mutations ---
  const jpost = (path: string, body?: unknown) =>
    fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined }).catch(() => {})
  const oqPath = (suffix = '') => `/api/threads/${encodeURIComponent(threadId)}/open-questions${suffix}`
  const patchOq = async (id: string, body: Record<string, unknown>) => {
    await fetch(oqPath(`/${id}`), { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).catch(() => {})
    onChange()
  }
  const promoteOq = async (id: string) => { await jpost(oqPath(`/${id}/promote`)); onChange() }
  const deleteOq = async (id: string) => { await fetch(oqPath(`/${id}`), { method: 'DELETE' }).catch(() => {}); onChange() }
  const addOq = async (text: string) => { await jpost(oqPath(), { text }); onChange() }
  const addClaim = async (text: string) => { await jpost('/api/claims', { statement: text, thread_id: threadId }); onChange() }
  const uploadExternal = async (f: File) => {
    const fd = new FormData(); fd.append('file', f); fd.append('thread_id', threadId); fd.append('interpretation', '')
    await fetch('/api/results/external', { method: 'POST', body: fd }).catch(() => {}); onChange()
  }

  // --- rows ---
  const thumbOf = (e: Entity): string | undefined => {
    if (e.type === 'figure' && e.artifact_path && IMG.test(e.artifact_path)) return e.artifact_path
    if (e.type === 'result') {
      const members = (e.metadata?.members as { kind: string; ref?: string }[]) ?? []
      const ref = members.find(m => m.kind === 'figure')?.ref
      const cell = ref ? byId.get(ref) : undefined
      return cell?.artifact_path && IMG.test(cell.artifact_path) ? cell.artifact_path : undefined
    }
    return undefined
  }
  const eRow = (e: Entity, tone?: Tone) => (
    <OverviewRow key={e.id} icon={e.type} label={e.title}
      thumb={thumbOf(e)} sub={(e.metadata?.interpretation as string) || undefined}
      badge={e.type === 'claim' ? String(e.metadata?.confidence || 'preliminary') : undefined}
      tone={tone ?? (e.metadata?.confidence === 'refuted' ? 'retired'
            : e.metadata?.confidence === 'contested' ? 'attention'
            : e.status === 'failed' ? 'attention' : undefined)}
      onClick={() => onGoTo(e.id)} title={text(e)}
      menu={<EntityMenu entity={e} onChange={onChange} />} />
  )

  const oqMenu = (o: OpenQ) => {
    const items: { label: string; onClick: () => void; danger?: boolean }[] = [
      { label: 'Discuss with Guide', onClick: () => onAsk(`Where do we stand on the open question: "${o.text}"? What have we learned so far, and what would resolve it?`) },
    ]
    if (o.status !== 'answered') items.push({ label: 'Mark answered', onClick: () => patchOq(o.id, { status: 'answered' }) })
    if (o.status === 'answered' || o.status === 'parked') items.push({ label: 'Reopen', onClick: () => patchOq(o.id, { status: 'open' }) })
    if (o.status !== 'parked' && o.status !== 'answered') items.push({ label: 'Park', onClick: () => patchOq(o.id, { status: 'parked' }) })
    if (o.status !== 'promoted') items.push({ label: 'Promote to thread', onClick: () => promoteOq(o.id) })
    if (o.status === 'promoted' && o.promoted_to) items.push({ label: 'Open its thread', onClick: () => onSelectThread(o.promoted_to!) })
    items.push({ label: 'Delete', onClick: () => deleteOq(o.id), danger: true })
    return <RowMenu items={items} />
  }
  const oqRow = (o: OpenQ, tone?: Tone): React.ReactNode => {
    if (editingOq === o.id) {
      return <InlineAdd key={o.id} placeholder="Edit the question…" value={o.text}
                        onSubmit={t => { setEditingOq(null); if (t.trim() && t.trim() !== o.text) patchOq(o.id, { text: t.trim() }) }}
                        onCancel={() => setEditingOq(null)} />
    }
    return (
      <OverviewRow key={o.id} icon="oq" label={o.text} tone={tone}
        onClick={() => setEditingOq(o.id)} title="Click to edit · ⋯ for actions"
        menu={oqMenu(o)} />
    )
  }

  // --- groups ---
  const pinnedGroups: OvGroup[] = [
    { label: 'Evidence', rows: pinned.filter(e => citedIds.has(e.id) && keep(e)).map(e => eRow(e)) },
    { label: 'Not yet used', rows: pinned.filter(e => !citedIds.has(e.id) && e.status !== 'superseded' && keep(e)).map(e => eRow(e)) },
    // The group already scopes to superseded, so don't re-apply the status
    // filter (which excludes superseded under 'active') — only honor search.
    { label: 'Superseded', tone: 'retired', rows: pinned.filter(e => e.status === 'superseded' && matches(text(e))).map(e => eRow(e, 'retired')) },
  ]
  const runStatus = (e: Entity) => ((e.metadata?.run as { status?: string })?.status)
    || (e.status === 'running' ? 'running' : e.status === 'failed' ? 'failed' : 'succeeded')
  const runRow = (e: Entity) => {
    const r = (e.metadata?.run ?? {}) as {
      executor?: string; where?: string; queue?: string
      outputs?: unknown[]; bulk?: { count?: number }
    }
    const hpc = r.executor === 'remote-hpc'
    const where = hpc ? `⛁ ${r.where || 'cluster'}${r.queue ? ` · ${r.queue}` : ''}` : '⚙ local'
    const n = r.outputs?.length ?? 0
    const outs = n > 0 ? `${n} output${n === 1 ? '' : 's'}${r.bulk?.count ? ` (+${r.bulk.count} files)` : ''}` : ''
    const sub = [where, outs].filter(Boolean).join(' · ')
    return (
      <OverviewRow key={e.id} icon="analysis" label={e.title} dot={runStatus(e)} sub={sub}
        onClick={() => onGoTo(e.id)} title={e.title}
        menu={<EntityMenu entity={e} onChange={onChange} />} />
    )
  }
  // Filter: 'active' hides cancelled; statuses give the at-a-glance monitor.
  const runVisible = (e: Entity) => matches(e.title) && (filter === 'all' || runStatus(e) !== 'cancelled')
  const runsBy = (s: string) => runs.filter(e => runStatus(e) === s && runVisible(e)).map(runRow)
  const runGroups: OvGroup[] = [
    { label: 'Running', tone: 'attention', rows: runsBy('running') },
    { label: 'Queued', rows: runsBy('queued') },
    { label: 'Complete', rows: runsBy('succeeded') },
    { label: 'Failed', tone: 'attention', rows: runsBy('failed') },
    { label: 'Cancelled', tone: 'retired', rows: runsBy('cancelled') },
  ]
  const clGroups: OvGroup[] = CONF_ORDER.map(conf => ({
    label: conf.charAt(0).toUpperCase() + conf.slice(1),
    tone: (conf === 'contested' ? 'attention' : conf === 'refuted' ? 'retired' : undefined) as Tone | undefined,
    rows: claims.filter(e => (e.metadata?.confidence as string || 'preliminary') === conf && keep(e)).map(e => eRow(e)),
  }))
  const oqShown = oqs.filter(o => matches(o.text) && (filter === 'all' || filter === 'active'))
  const oqGroups: OvGroup[] = [
    { label: 'Open', rows: oqShown.filter(o => (o.status || 'open') === 'open').map(o => oqRow(o)) },
    { label: 'Answered', tone: 'retired', rows: oqShown.filter(o => o.status === 'answered').map(o => oqRow(o, 'retired')) },
    { label: 'Parked', tone: 'retired', rows: oqShown.filter(o => o.status === 'parked').map(o => oqRow(o, 'retired')) },
    { label: 'Promoted', rows: oqShown.filter(o => o.status === 'promoted').map(o => oqRow(o)) },
  ]

  const question = (thread.metadata?.question as string) || ''
  const lifecycle = (thread.metadata?.lifecycle as string) || 'open'
  const CHIPS: Filter[] = ['attention', 'active', 'contested', 'all']
  const setLifecycle = async (lc: string) => {
    await fetch(`/api/threads/${encodeURIComponent(threadId)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lifecycle: lc }),
    }).catch(() => {})
    onChange()
  }

  return (
    <div className="overview">
      <div className="overview__bar overview__bar--thread">
        <div className="overview__thread-q">
          {question ? <span className="overview__q">{question}</span>
                    : <span className="overview__q is-empty">No question framed yet</span>}
          <div className="overview__counts">
            <span><b>{pinned.length}</b> Pinned</span>
            <span><b>{runs.length}</b> Runs</span>
            <span><b>{claims.length}</b> Claims</span>
            <span><b>{oqs.length}</b> Open Qs</span>
            <span className="overview__lc">
              {['open', 'parked', 'concluded'].map(s => (
                <button key={s} className={`overview__lc-btn ${lifecycle === s ? 'is-on' : ''}`}
                        onClick={() => setLifecycle(s)} title={`Mark thread ${s}`}>{s}</button>
              ))}
            </span>
          </div>
        </div>
        <input className="overview__search" placeholder="Search this thread…"
               value={query} onChange={e => setQuery(e.target.value)} autoFocus />
        <div className="overview__chips">
          {CHIPS.map(c => (
            <button key={c} className={`overview__chip ${filter === c ? 'is-on' : ''}`}
                    onClick={() => setFilter(c)}>{c}</button>
          ))}
        </div>
      </div>
      <div className="overview__cols overview__cols--4">
        <OverviewColumn title="Pinned" total={pinned.length} groups={pinnedGroups}
                        onAdd={() => setPinnedDialog(true)} addTitle="Add a result"
                        emptyHint="Pin figures from the chat to build evidence." />
        <OverviewColumn title="Runs" total={runs.length} groups={runGroups} emptyHint="No analyses recorded." />
        <OverviewColumn title="Claims" total={claims.length} groups={clGroups}
                        onAddText={addClaim} addPlaceholder="State the claim…" addTitle="New claim" emptyHint="No claims yet." />
        <OverviewColumn title="Open Questions" total={oqs.length} groups={oqGroups}
                        onAddText={addOq} addPlaceholder="New open question…" addTitle="Add an open question"
                        emptyHint="No open questions yet." />
      </div>
      {pinnedDialog && (
        <AddResourceDialog title="Add a result"
          describeLabel="Or describe an external result and let Guide bring it in:"
          onUpload={uploadExternal} onAsk={onAsk} onClose={() => setPinnedDialog(false)} />
      )}
    </div>
  )
}
