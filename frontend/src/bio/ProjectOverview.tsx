/**
 * Project overview (ui3 P8) — a full-canvas, status-grouped map of the whole
 * project: Datasets · Threads · Claims (Manuscript deferred until the entity
 * exists). Search + filter chips across all columns; clicking any item closes
 * the overview and navigates to it. Pure client-side over `entities`.
 */
import { useState } from 'react'
import type { Entity } from '../types'
import { OverviewColumn, OverviewRow, AddResourceDialog, type OvGroup, type Tone } from '../components/OverviewKit'
import EntityMenu from '../components/EntityMenu'
import './ProjectOverview.css'

interface Props {
  entities: Entity[]
  onGoTo: (id: string) => void
  /** Clicking a thread row should enter that thread (chat-centric layout),
   *  matching what a Threads-tab click does. Without this, the row would
   *  go through onGoTo → openEntity → focus-the-thread-entity, which puts
   *  the thread in the right column instead of switching to its chat
   *  (PK 2026-06-03 — wanted consistency with the rail behavior). */
  onSelectThread: (id: string) => void
  onClose: () => void
  onChange: () => void
  onAsk: (text: string) => void
}

async function jpost(path: string, body: unknown) {
  await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).catch(() => {})
}

type Filter = 'attention' | 'active' | 'contested' | 'all'
const CONF_ORDER = ['validated', 'contested', 'supported', 'preliminary', 'refuted'] as const

const isAttention = (e: Entity) =>
  e.metadata?.confidence === 'contested' || e.metadata?.confidence === 'refuted'

export default function ProjectOverview({ entities, onGoTo, onSelectThread, onChange, onAsk }: Props) {
  const [dataDialog, setDataDialog] = useState(false)
  const addThread = async (text: string) => {
    await jpost('/api/threads', { title: text.slice(0, 60), question: text, question_source: 'user' })
    onChange()
  }
  const addClaim = async (text: string) => { await jpost('/api/claims', { statement: text, thread_id: 'default' }); onChange() }
  const uploadDataset = async (f: File) => {
    const fd = new FormData(); fd.append('file', f)
    await fetch('/api/upload', { method: 'POST', body: fd }).catch(() => {})
    onChange()
  }

  const datasets = entities.filter(e => e.type === 'dataset')
  const threads = entities.filter(e => e.type === 'thread')
  const claims = entities.filter(e => e.type === 'claim')

  // Default to the working set (Active); 'attention' is an explicit chip.
  const [filter, setFilter] = useState<Filter>('active')
  const [query, setQuery] = useState('')

  const q = query.trim().toLowerCase()
  const matches = (e: Entity) => {
    if (!q) return true
    const stmt = (e.metadata?.statement as string) || ''
    return e.title.toLowerCase().includes(q) || stmt.toLowerCase().includes(q)
  }
  const passesFilter = (e: Entity) => {
    if (filter === 'all') return true
    const archived = e.status === 'archived'
    const conf = e.metadata?.confidence as string | undefined
    if (filter === 'active') return !archived && conf !== 'refuted'
    if (filter === 'contested') return conf === 'contested'
    return isAttention(e)
  }
  const keep = (e: Entity) => matches(e) && passesFilter(e)

  const rowFor = (e: Entity): React.ReactNode => (
    <OverviewRow key={e.id} icon={e.type}
      label={(e.metadata?.statement as string) || e.title}
      badge={e.type === 'claim' ? String(e.metadata?.confidence || 'preliminary') : undefined}
      tone={e.metadata?.confidence === 'refuted' ? 'retired' : isAttention(e) ? 'attention' : undefined}
      // Threads → enter the thread (chat-centric layout, matches the rail
      // Threads-tab behavior). Everything else → entity-first center view.
      onClick={() => e.type === 'thread' ? onSelectThread(e.id) : onGoTo(e.id)}
      menu={<EntityMenu entity={e} onChange={onChange} />} />
  )
  const grp = (label: string, items: Entity[], tone?: Tone): OvGroup =>
    ({ label, tone, rows: items.filter(keep).map(rowFor) })

  const lc = (t: Entity) => (t.metadata?.lifecycle as string) || 'open'
  const dsGroups = [
    grp('Active', datasets.filter(e => e.status !== 'archived')),
    grp('Archived', datasets.filter(e => e.status === 'archived'), 'retired'),
  ]
  const thGroups = [
    grp('Active', threads.filter(e => e.status !== 'archived' && lc(e) === 'open')),
    grp('Parked', threads.filter(e => e.status !== 'archived' && lc(e) === 'parked')),
    grp('Concluded', threads.filter(e => e.status !== 'archived' && lc(e) === 'concluded')),
    grp('Archived', threads.filter(e => e.status === 'archived'), 'retired'),
  ]
  const clGroups = CONF_ORDER.map(conf => grp(
    conf.charAt(0).toUpperCase() + conf.slice(1),
    claims.filter(e => (e.metadata?.confidence as string || 'preliminary') === conf),
    conf === 'contested' ? 'attention' : conf === 'refuted' ? 'retired' : undefined,
  ))

  const CHIPS: Filter[] = ['attention', 'active', 'contested', 'all']

  return (
    <div className="overview">
      <div className="overview__bar">
        <div className="overview__counts">
          <span><b>{threads.length}</b> Threads</span>
          <span><b>{claims.length}</b> Claims</span>
          <span><b>{datasets.length}</b> Datasets</span>
        </div>
        <input className="overview__search" placeholder="Search threads, claims, datasets…"
               value={query} onChange={e => setQuery(e.target.value)} autoFocus />
        <div className="overview__chips">
          {CHIPS.map(c => (
            <button key={c} className={`overview__chip ${filter === c ? 'is-on' : ''}`}
                    onClick={() => setFilter(c)}>{c}</button>
          ))}
        </div>
      </div>
      {/* Order mirrors the rail: inquiry first (Threads → Claims), data last.
          Manuscript will slot in before Datasets when that entity lands. */}
      <div className="overview__cols overview__cols--3">
        <OverviewColumn title="Threads" total={threads.length} groups={thGroups}
                        onAddText={addThread} addPlaceholder="What question is this thread investigating?" addTitle="New thread" />
        <OverviewColumn title="Claims" total={claims.length} groups={clGroups}
                        onAddText={addClaim} addPlaceholder="State the claim…" addTitle="New claim" />
        <OverviewColumn title="Datasets" total={datasets.length} groups={dsGroups}
                        onAdd={() => setDataDialog(true)} addTitle="Add data" />
      </div>
      {dataDialog && (
        <AddResourceDialog title="Add data" describeLabel="Or describe the data you want and let Guide find/fetch it:"
          onUpload={uploadDataset} onAsk={onAsk} onClose={() => setDataDialog(false)} />
      )}
    </div>
  )
}
