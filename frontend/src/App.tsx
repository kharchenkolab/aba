import { useState, useEffect, useRef } from 'react'
import { useUrlState } from './useUrlState'
import { useResetOnChange } from './hooks/useResetOnChange'
import './App.css'
import Rail from './components/Rail'
import ProjectTree from './components/ProjectTree'
import ChatPane from './components/ChatPane'
import AdvisorStrip from './components/AdvisorStrip'
import FocusCanvas from './components/FocusCanvas'
import FileCanvas from './viewers/FileCanvas'
import type { FileNode } from './viewers/types'
import Home from './components/Home'
import HResizer from './components/HResizer'
import PostureToggle, { type Posture } from './components/PostureToggle'
import SearchModal from './components/SearchModal'
import ThreadHeader from './components/ThreadHeader'
import PinnedShelf from './bio/PinnedShelf'
import Drawer from './components/Drawer'
import ThreadOverview from './bio/ThreadOverview'
import ProjectOverview from './bio/ProjectOverview'
import { useProposals, ProposalCard, UndoToast } from './components/Proposals'
import { useChat } from './useChat'
import { useEntities } from './useEntities'
import type { Entity } from './types'

const TREE_DEFAULT = 240
const TREE_MIN = 150
const RIGHT_DEFAULT = 384
const RIGHT_MIN = 280
const RIGHT_MAX = 560
type ProjectSection = 'threads' | 'claims' | 'data' | 'runs' | 'results' | 'files'

/** Walk a files-tree response to find the node at the given POSIX path.
 *  Used by Phase 2 routing to hydrate the viewedFile when the user lands
 *  on /p/<pid>/files/<path> directly (deep link, reload, Back). */
function findNodeByPath(root: FileNode | undefined, path: string): FileNode | null {
  if (!root || !path) return null
  type N = FileNode & { children?: N[] }
  const stack: N[] = [root as N]
  while (stack.length) {
    const n = stack.pop()!
    if (n.path === path) return n
    if (n.children) for (const c of n.children) stack.push(c)
  }
  return null
}

// Display label for an entity type — note `analysis` reads as "Run" (the v3
// "analysis run"), avoiding confusion with the thread/investigation idea.
function typeLabel(t?: string): string {
  switch (t) {
    case 'figure': return 'Figure'
    case 'table': return 'Table'
    case 'finding': return 'Finding'
    case 'result': return 'Result'
    case 'dataset': return 'Dataset'
    case 'narrative': return 'Section'
    case 'analysis': return 'Run'
    case 'claim': return 'Claim'
    case 'thread': return 'Thread'
    default: return 'Entity'
  }
}

function entityLabel(e: Entity | null): string {
  switch (e?.type) {
    case 'figure': return 'Figure'
    case 'table': return 'Table'
    case 'finding': return 'Finding'
    case 'result': return 'Result'
    case 'dataset': return 'Dataset'
    case 'narrative': return 'Section'
    case 'analysis': return 'Run'
    default: return 'Entity'
  }
}

/** Central-header thread title — click to rename inline (mirrors ResultView). */
function EditableThreadTitle({ thread, onRenamed }: {
  thread: { id: string; title: string }; onRenamed: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(thread.title)
  async function save() {
    const t = draft.trim()
    setEditing(false)
    if (!t || t === thread.title) return
    await fetch(`/api/threads/${encodeURIComponent(thread.id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: t }),
    })
    onRenamed()
  }
  // Edit IN PLACE: keep the "thread" pill, swap only the title text for an
  // input that fills the remaining width — so nothing jumps.
  return (
    <>
      <span className="canvas-title__type">thread</span>
      {editing ? (
        <input className="canvas-title__input" autoFocus value={draft}
               onFocus={e => e.currentTarget.select()}
               onChange={e => setDraft(e.target.value)} onBlur={save}
               onKeyDown={e => {
                 if (e.key === 'Enter') save()
                 if (e.key === 'Escape') { setDraft(thread.title); setEditing(false) }
               }} />
      ) : (
        <span className="canvas-title__editable"
              onClick={() => { setDraft(thread.title); setEditing(true) }}
              title="Click to rename">{thread.title}</span>
      )}
    </>
  )
}

export default function App() {
  // Phase 1 routing: URL is canonical for project / thread / focused entity.
  // The legacy variable names (focusedId, threadId, view, projectKey) are
  // preserved so the dozens of existing call sites don't churn — but their
  // values now come from / write to the URL via useUrlState.
  const url = useUrlState()
  const focusedId   = url.focusedId
  const setFocusedId = url.setFocus
  const threadId    = url.threadId
  const setThreadId = url.setThread
  const view: 'home' | 'workspace' = url.isHome ? 'home' : 'workspace'
  // Remount-key for the chat thread: changes whenever the URL project changes,
  // forcing useChat to discard in-flight stream + refetch the new project's
  // conversation. '_home' just keeps the key non-empty when no project.
  const projectKey = url.pid ?? '_home'

  // Rail tab is URL-driven (it's just url.section). Tab clicks push a
  // history entry, but tab → item-click coalesces inside useUrlState
  // (setSection marks the navigation as eligible; the next setFocus
  // within ~1.5 s replaces instead of pushing), so one Back unwinds
  // the whole "I flipped to Runs and clicked a run" exploration.
  const projectSection = url.section as ProjectSection
  const setProjectSection = (s: ProjectSection) => url.setSection(s)

  const overview  = url.scene === 'overview'
  const inventory = url.scene === 'inventory'
  const setOverview  = (on: boolean) => url.setScene(on ? 'overview'  : null)
  const setInventory = (on: boolean) => url.setScene(on ? 'inventory' : null)
  // Synthesized / non-entity file currently viewed in the central column.
  // The URL is canonical for WHICH file (under /p/<pid>/files/<path>); the
  // FileNode object is hydrated lazily from /api/files/tree so FileCanvas
  // can render synthesized content (README bodies, claim markdown, etc.).
  // Clicking a file in FilesView passes the full node here directly via
  // viewFile() so the round-trip via /api/files/tree only fires for
  // deep-link reload / Back navigation.
  const [viewedFile, setViewedFile] = useState<FileNode | null>(null)
  useEffect(() => {
    const path = url.filePath
    if (!path) { setViewedFile(null); return }
    if (viewedFile?.path === path) return    // already have the node from a click
    let cancelled = false
    fetch(`/api/files/tree${url.pid ? `?project_id=${encodeURIComponent(url.pid)}` : ''}`)
      .then(r => r.json())
      .then((root: FileNode) => {
        if (cancelled) return
        const found = findNodeByPath(root, path)
        // Even if we can't find the node (stale tree, deleted file), set a
        // stub so FileCanvas can attempt to render via /api/files/raw.
        setViewedFile(found ?? { kind: 'file', name: path.split('/').pop() || path, path })
      })
      .catch(() => { /* ignore — central column will fall back to placeholder */ })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url.filePath, url.pid])
  const [posture, setPosture] = useState<Posture>('chat')
  const [highlighting, setHighlighting] = useState(false)
  const [treeW, setTreeW] = useState(TREE_DEFAULT)
  const [treeCollapsed, setTreeCollapsed] = useState(false)
  const [rightW, setRightW] = useState(RIGHT_DEFAULT)
  // Right column (thread-context / chat-peek) collapse. Policy: collapsing is a
  // transient "give me room for THIS view" gesture — it auto-reopens whenever the
  // view context changes (posture, focused entity, thread, overview/inventory), so
  // the user never loses the contextual right column silently after navigating.
  const [rightCollapsed, _setRightCollapsedRaw] = useState(false)
  // Sticky user-collapse: once the user explicitly hides the right rail in
  // THIS browser session, no automatic in-session trigger (focus shift,
  // agent-driven entity update, scene change, first-downstream-output reveal)
  // reopens it. Session-scoped only — no localStorage persistence (PK
  // 2026-06-03: that crossed a line; UI state shouldn't survive reload).
  // On a fresh page, frameOnProjectEntry's CONTENT-driven decision is the
  // sole authority; sticky doesn't enter the picture.
  const userCollapsedRef = useRef(false)
  // For automated openers — bail when the user has stickied collapsed.
  const autoRevealRail = () => {
    if (userCollapsedRef.current) return
    _setRightCollapsedRaw(false)
  }
  // User-driven toggle: sets the in-session sticky bit. Collapsing sets it
  // true (in-session auto-reveals will respect it); expanding clears it.
  const userToggleRail = () => {
    _setRightCollapsedRaw(prev => {
      const next = !prev
      userCollapsedRef.current = next
      return next
    })
  }
  // Programmatic project-entry framing — sets state + sticky. The rail
  // doesn't get auto-revealed by drive-by effects on a freshly framed
  // project unless the user toggles back open.
  const frameRail = (collapsed: boolean) => {
    userCollapsedRef.current = collapsed
    _setRightCollapsedRaw(collapsed)
  }
  // Transient close (no sticky touch) — used by the pre-load flicker hide
  // that the URL-change effect issues SYNCHRONOUSLY before /api/entities
  // returns. Leaving sticky alone lets the post-load frameRail honor the
  // genuine user intent (or lack thereof).
  const _setRightTransient = (collapsed: boolean) => {
    _setRightCollapsedRaw(collapsed)
  }
  const [prefill, setPrefill] = useState('')
  // Files-tab deep-link target (e.g. a Run's "Browse in Files tab"); nonce so a
  // repeat click to the same path re-navigates.
  const [filesTarget, setFilesTarget] = useState<{ path: string; n: number }>({ path: '', n: 0 })
  const [composerFocus, setComposerFocus] = useState(0)
  const [annotClear, setAnnotClear] = useState(0)
  const attachAnnotation = (a: { image: string; note: string }) => {
    setAnnotation(a)
    setComposerFocus(n => n + 1)   // jump the cursor to the composer
  }
  const clearAnnotation = () => {
    setAnnotation(null)
    setAnnotClear(n => n + 1)      // erase the drawn mark on the figure too
  }
  const [annotation, setAnnotation] = useState<{ image: string; note: string } | null>(null)
  const [searchOpen, setSearchOpen] = useState(false)
  const [hasProject, setHasProject] = useState(true)
  const [chatReload, setChatReload] = useState(0)       // bump to refetch the thread's messages
  const orientedRef = useRef<Set<string>>(new Set())    // cold-start orient attempts
  const { entities, refresh } = useEntities(url.pid ?? undefined)

  // Active (non-archived) entities + the content tallies the "start-slowly"
  // framing needs. Computed up here (before the Home early-return) so the
  // collapse effects below can read them; sectionCounts reuses activeEntities.
  const activeEntities = entities.filter(e => e.status !== 'archived' && e.status !== 'superseded')
  const datasetCount = activeEntities.filter(e => e.type === 'dataset').length
  const downstreamCount = activeEntities.filter(e =>
    ['figure', 'table', 'result', 'note', 'narrative', 'analysis', 'claim'].includes(e.type)).length

  const refreshCurrent = () => {
    fetch('/api/projects/current')
      .then(r => r.json())
      .then(d => setHasProject(!!d.current))
      .catch(() => {})
  }
  useEffect(() => { refreshCurrent() }, [])
  // Phase 4.6: prime the entity-type catalog once on app mount so
  // subsequent lookups in shell components (EntityMenu, etc.) hit
  // the cache instead of falling back to the legacy hardcoded set.
  useEffect(() => {
    import('./entityTypes').then(m => m.loadEntityTypes().catch(() => {}))
  }, [])

  // Subscribe to /api/notifications — the global push channel for
  // out-of-band events (caption ready, background job done, …). Replaces
  // the prior "guess a refresh delay" hack. One EventSource per app
  // lifetime; the browser handles reconnect on transient drops.
  useEffect(() => {
    const es = new EventSource('/api/notifications')
    es.onmessage = (msg) => {
      try {
        const ev = JSON.parse(msg.data)
        if (ev.type === 'entity_updated') refresh()
      } catch {}
    }
    return () => { es.close() }
  }, [refresh])

  // posture follows focus: entity-first when something is focused (or a
  // file is being viewed); chat-first otherwise. PostureToggle can still
  // override manually within a given URL state. Re-derived on every URL
  // change because focusedId / viewedFile flip with the route.
  useEffect(() => {
    setPosture((focusedId !== 'workspace' || viewedFile) ? 'entity' : 'chat')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusedId, viewedFile])

  // Clear any in-flight annotation (image + framing note) when focus
  // changes — see hooks/useResetOnChange for full rationale.
  useResetOnChange(focusedId, () => setAnnotation(null))

  // Root URL ("/") = the project selector. We deliberately do NOT auto-
  // redirect into the server-side current project; that would flash the
  // user past Home on every fresh tab. Reload of /p/<pid> preserves the
  // URL on its own; Home is the explicit re-entry point.

  // URL pid changed: sync the server-side current project, reset transient
  // UI state, and refresh entities. Idempotent — a no-op when pid matches
  // the server already.
  useEffect(() => {
    if (!url.pid) return
    const pid = url.pid
    setAnnotation(null)
    // Do NOT reset posture or scene here — they're derived from the URL.
    // A reload of /p/<pid>/data/e/<did> deliberately encodes "data tab, this
    // dataset focused"; clobbering posture to 'chat' here desyncs the central
    // column from the rail (rail stays on the dataset row but central reverts
    // to chat, and re-clicking the already-selected row is a no-op). Same
    // shape for setInventory(false) — it mutates url.scene out from under a
    // reload landing on an inventory view. The focusedId-driven useEffect
    // above is the sole authority for posture; url.scene is the sole
    // authority for overview/inventory. (2026-06-04 reload-clobber fix.)
    // Collapse both columns SYNCHRONOUSLY on project entry so the empty-project
    // "zoom on chat" is in effect during the entity-fetch window; frameOnProjectEntry
    // (below) is the sole authority to re-open them once it sees the entity counts.
    // Use _setRightTransient for the rail — this is a flicker-prevention close,
    // NOT a user-intent collapse, so it must not flip the sticky bit (which
    // would then refuse the post-load frameRail(false) for established projects).
    setTreeCollapsed(true); _setRightTransient(true)
    fetch('/api/projects/current')
      .then(r => r.json())
      .then(d => {
        if (d.current !== pid) {
          return fetch(`/api/projects/${encodeURIComponent(pid)}/open`, { method: 'POST' })
        }
      })
      .then(() => { refresh(); refreshCurrent() })
      // Start-slowly framing from a FRESH snapshot — an empty `entities` array
      // can't tell "still loading" from "genuinely empty", so we re-fetch here.
      .then(() => fetch(`/api/entities?project_id=${encodeURIComponent(pid)}`))
      .then(r => (r && r.ok) ? r.json() : [])
      .then((rows: { status?: string; type?: string }[]) => frameOnProjectEntry(rows))
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url.pid])

  // Apply the initial column framing when a project is opened. Two decisions,
  // made INDEPENDENTLY:
  //
  // Left tree:
  //   • empty project              → collapsed (focus the conversation)
  //   • dataset(s), no output yet  → open on Data tab (orient to data)
  //   • established (has output)   → open
  //
  // Right rail (PK 2026-06-03): the rail's job is to surface user-curated
  //   content — currently pinned figures and the user-set Question. If
  //   either exists, reveal. Otherwise stay collapsed — the rail hosting
  //   only AI-generated content (a guide-refined question, no pins) isn't
  //   worth taking screen real-estate from the conversation by default.
  //   The user can always toggle it open.
  type _Row = { type?: string; status?: string; metadata?: Record<string, unknown> }
  const frameOnProjectEntry = (rows: _Row[]) => {
    const active = rows.filter(e => e.status !== 'archived' && e.status !== 'superseded')
    const ds = active.filter(e => e.type === 'dataset').length
    const downstream = active.filter(e =>
      ['figure', 'table', 'result', 'note', 'narrative', 'analysis', 'claim'].includes(e.type ?? '')).length
    const threadCount = active.filter(e => e.type === 'thread').length

    // Left tree:
    //   • empty project              → collapsed (focus the conversation)
    //   • dataset(s), no output yet  → open on Data tab (orient to data)
    //   • established (has output)   → open, UNLESS the user is on the Threads
    //     tab with ≤1 thread — there's nothing to navigate in the rail then,
    //     so collapse and let the chat have the room (PK 2026-06-03).
    // "Bare URL" = /p/<pid> with no explicit section / thread / entity / scene.
    // Reload of a deep URL counts as NOT-bare; respect what the URL encodes
    // (don't auto-snap a data-only project to the Data tab if the user
    // explicitly landed on Threads or focused an entity).
    const bareUrl = url.eid === 'workspace' && url.tid === 'default'
      && url.scene === null && url.section === 'threads'
    if (downstream > 0) {
      const onThreadsTab = projectSection === 'threads'
      setTreeCollapsed(onThreadsTab && threadCount <= 1)
    }
    else if (ds > 0)    {
      setTreeCollapsed(false)
      if (bareUrl) setProjectSection('data')
    }
    else                setTreeCollapsed(true)

    // Right rail — content-driven.
    const hasPinnedFigure = active.some(e =>
      e.type === 'result' &&
      Array.isArray(e.metadata?.members) &&
      (e.metadata!.members as Array<{ kind?: string }>).some(m => m.kind === 'figure'))
    const hasUserQuestion = active.some(e =>
      e.type === 'thread' && (e.metadata?.question_source === 'user'))
    frameRail(!(hasPinnedFigure || hasUserQuestion))
  }

  // Enter a project picked in Home: pure navigation; the useEffect above
  // does the server sync + state reset.
  const enterProject = (pid: string) => { url.setProject(pid) }

  // Rail nav: there's no project to open in the true empty state, so the
  // "Project" item falls back to Home until one exists.
  // Guard against a subtle race: Rail's tab buttons call
  // onNavigate('workspace') alongside onProjectSection(s). Without the
  // url.pid no-op below, the async setProject(currentPid) would land
  // *after* the section change and clobber it — flashing Runs and snapping
  // back to default Threads. When we already have a pid, "go to workspace"
  // is implicit; nothing to do.
  const goToView = (v: 'home' | 'workspace') => {
    if (v === 'home') { url.goHome(); return }
    if (url.pid) return
    if (!hasProject) { url.goHome(); return }
    fetch('/api/projects/current')
      .then(r => r.json())
      .then(d => { if (d.current) url.setProject(d.current) })
      .catch(() => {})
  }

  // Cmd/Ctrl-K opens fallback search.
  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault(); setSearchOpen(o => !o)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])
  const { messages, streaming, streamMsg, sendMessage, retryLast, loading: chatLoading, manifest,
          pendingClarification, answerClarification,
          pendingApproval, respondApproval, stopTurn,
          queuedMessage, enqueue, dropQueue, steer,
          eventLog, jobs, currentRunId } = useChat(
    focusedId, refresh, annotation, `${projectKey}:${chatReload}`, threadId, url.pid ?? undefined,
  )
  const [drawerOpen, setDrawerOpen] = useState(false)

  // Right column auto-reopens ONLY when the user navigates to a scene that
  // lives in the right column (overview, inventory). It does NOT reopen on
  // thread switch / focus change / posture change — those are chat-pane
  // navigation, and the right column may have nothing to do with the switch.
  // (PK 2026-06-02: minimized rail was popping out on every thread switch.)
  // AI-driven data growth also doesn't reopen (the dsCountRef gate already
  // handles that). Earn-it first reveal stays in the separate effect below.
  const dsCountRef = useRef(downstreamCount)
  useEffect(() => { dsCountRef.current = downstreamCount }, [downstreamCount])
  useEffect(() => {
    if (dsCountRef.current === 0) return
    if (!overview && !inventory) return    // only when the active scene IS one that lives in the rail
    autoRevealRail()                       // respects sticky user-collapse
  }, [overview, inventory])
  // Earn-it first reveal: edge-triggered on the 0→>0 downstreamCount transition.
  // Fires once when the project produces its first downstream output, then
  // subsequent additions are silent (the AI growing the inventory doesn't
  // reopen a user-collapsed rail).
  const prevDsCountRef = useRef(downstreamCount)
  useEffect(() => {
    if (prevDsCountRef.current === 0 && downstreamCount > 0) {
      autoRevealRail()                     // respects sticky user-collapse
    }
    prevDsCountRef.current = downstreamCount
  }, [downstreamCount])

  // Mid-session: the first dataset landing in a still-fresh project reveals the
  // tree on the Data tab (mirrors the project-entry framing). Edge-triggered on
  // the 0→>0 transition; null start means it never fires on initial mount.
  const prevDatasetCount = useRef<number | null>(null)
  useEffect(() => {
    const prev = prevDatasetCount.current
    prevDatasetCount.current = datasetCount
    if (prev === 0 && datasetCount > 0 && downstreamCount === 0) {
      setTreeCollapsed(false)
      setProjectSection('data')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetCount, downstreamCount])

  // Cold-start orientation: when a thread has data but no conversation yet, ask
  // the Guide to post an opening summary + next steps. Idempotent server-side;
  // we attempt once per (project, thread) and refetch the chat if it posted.
  useEffect(() => {
    if (view !== 'workspace' || streaming) return
    if (messages.length > 0) return
    if (!entities.some(e => e.type === 'dataset')) return
    const key = `${projectKey}:${threadId}`
    if (orientedRef.current.has(key)) return
    orientedRef.current.add(key)
    fetch(`/api/threads/${encodeURIComponent(threadId)}/orient`, { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        if (d.oriented) {
          setChatReload(n => n + 1)   // reload the conversation (the new message)
          refresh()                    // reload entities — the thread now carries orient_steps (the chips)
        }
      })
      .catch(() => {})
  }, [view, streaming, messages.length, entities, threadId, projectKey])

  const focused = entities.find(e => e.id === focusedId) ?? null
  const scoped = !!focused && focused.type !== 'workspace'
  const projectName = entities.find(e => e.type === 'workspace')?.title || 'Project'
  // 'default' resolves to the materialized default-thread entity (if it exists),
  // so Main graduates to a header once it has a question.
  const currentThread = threadId !== 'default'
    ? entities.find(e => e.id === threadId && e.type === 'thread') ?? null
    : entities.find(e => e.type === 'thread' && !!e.metadata?.is_default) ?? null

  // Proactive proposals (Phase D): polled per thread; accepting one changes the
  // world (a claim, a question, an OQ) so we refresh entities on accept/undo.
  const { proposals, undoable, accept: acceptProposal, dismiss: dismissProposal,
          undo: undoProposal, clearUndo } = useProposals(currentThread?.id ?? null, refresh)

  // Phase 2 routing: setFocus / setThread / setSection in useUrlState all
  // drop the active scene as part of their navigation, so explicit
  // "exitModes()" calls are no longer needed before a focus/thread change.

  // Everything opens in the center (entity-first); the chat moves to the right
  // peek and "← Back to thread" exits. There is no chat-first preview slot.
  const openEntity = (id: string) => { setFocusedId(id) }

  // Selecting a thread is entering a line of inquiry: switching threads
  // also clears focus (handled by setThread). Posture re-derives from focus.
  const selectThread = (id: string) => {
    setThreadId(id)
    // Thread-open event trigger (Phase D): may surface a return-wrap proposal.
    fetch(`/api/threads/${encodeURIComponent(id)}/evaluate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trigger: 'thread_open' }),
    }).catch(() => {})
  }

  // Shortcut from the rail: jump straight to a thread's inventory. One
  // navigation (single history entry) via setNav: switches thread, clears
  // any focus, sets scene=inventory atomically.
  const openThreadOverview = (id: string) => {
    if (!url.pid) return
    fetch(`/api/threads/${encodeURIComponent(id)}/evaluate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trigger: 'thread_open' }),
    }).catch(() => {})
    url.setNav({ threadId: id, focusedId: 'workspace', scene: 'inventory' })
  }

  // Navigate to a claim *in its own context*: switch to its home thread + open
  // entity in one navigation (setThreadAndFocus also drops any scene).
  const openClaim = (id: string) => {
    const c = entities.find(e => e.id === id)
    const home = c?.metadata?.thread_id as string | undefined
    let newTid = threadId
    if (home) {
      const homeEnt = entities.find(e => e.id === home)
      newTid = homeEnt?.metadata?.is_default ? 'default' : home
    }
    url.setThreadAndFocus(newTid, id)
  }
  // Rail/list/shelf click router: everything opens in the CENTER (entity-first),
  // with the chat moving to the right peek and "← Back to thread" as the exit.
  // The right column stays thread-context (brief + shelf); it never becomes a
  // preview slot — that's what created the in-place "expand with no way out".
  const goToEntity = (id: string) => {
    setViewedFile(null)  // clear any synthesized-file view first
    const e = entities.find(x => x.id === id)
    if (e?.type === 'claim') openClaim(id)
    else openEntity(id)
  }

  // Open a non-entity tree node in the central column (FileCanvas).
  // Entity-backed nodes go through goToEntity instead — see FilesView.
  // Sets local state for the immediate render (so we have the rich node
  // including synthesized content) and navigates the URL so reload /
  // bookmark / Back land on the same file.
  const viewFile = (node: FileNode) => {
    setViewedFile(node)
    url.setFilePath(node.path)
  }
  // From an entity (entity-first) back to its thread's conversation.
  const backToThread = () => { setFocusedId('workspace') }

  // Hand a request to the Guide (used by overview "describe a resource" /
  // "discuss this question"): leave any mode, drop to chat, send the message.
  const askGuide = (text: string) => {
    setFocusedId('workspace')
    sendMessage(text)
  }

  // "Chat" gesture on a run output: bring the plot into the Guide chat. We fetch
  // the (same-origin) image and attach it so the Guide can SEE it, then PREFILL
  // the composer (focused) — the user hits Enter as-is or types their actual
  // question first. We don't auto-send. Non-image/remote outputs prefill only.
  //
  // Crucially, don't disturb the layout: in a Run (entity) view the Guide pane
  // is already on screen as the peek, so we prefill *that* and leave the Run up.
  // We only reveal the chat when a full-canvas mode (overview/inventory) is
  // currently hiding it.
  // Action variants for the figure SplitButton (Stage 5 of
  // misc/exec_records_and_versioning.md). Default 'chat' preserves the
  // pre-existing flow; 'revision' and 'reproduce' carry the agent to
  // the make_revision / reproduce_from_exec tools via a tailored prefill.
  // 'revision-supersede' is the user-confirmed branch when revising from
  // a NON-LATEST revision in the chain; it tells the agent to pass
  // supersede_newer=True to make_revision (which marks the displaced
  // newer revisions as status='superseded' so the visible chain stays
  // linear). The confirmation dialog lives in RevisionStrip.
  type FigureAction = 'chat' | 'revision' | 'revision-supersede' | 'reproduce'
  const chatAboutResult = async (
    label: string,
    thumb?: string,
    annotation?: { image: string; note: string },
    action: FigureAction = 'chat',
    entityId?: string,
  ) => {
    if (overview || inventory) { setFocusedId('workspace') }
    // Build a precise entity-id clause when we have one. Including it
    // explicitly removes the agent's guesswork (the focused entity is
    // often the Result, not the figure the user clicked on).
    const idClause = entityId ? ` (entity_id="${entityId}")` : ''
    if (annotation) {
      // Already-composited image (e.g. a highlighted region) — attach as-is.
      attachAnnotation(annotation)
    } else if (thumb) {
      try {
        const blob = await (await fetch(thumb)).blob()
        if (blob.type.startsWith('image/')) {
          const b64: string = await new Promise((res, rej) => {
            const fr = new FileReader()
            fr.onerror = rej
            fr.onload = () => res(String(fr.result).split(',')[1] ?? '')
            fr.readAsDataURL(blob)
          })
          if (b64) {
            const note = action === 'revision-supersede'
              ? `The user has explicitly CONFIRMED revising from a non-latest revision of "${label}"${idClause}. Any newer revisions will be displaced. Call make_revision(entity_id, modified_code, supersede_newer=True). The attached image is the revision the user is revising FROM.`
              : action === 'revision'
              ? `The user wants a revision of "${label}"${idClause}. The attached image is the current figure — examine it, then call make_revision(entity_id, modified_code) with modified code.`
              : action === 'reproduce'
              ? `The user wants to reproduce "${label}"${idClause}. The attached image is the current figure — call reproduce_from_exec(entity_id) and report any drift.`
              : `The user is asking about the run output "${label}"${idClause}. The attached image is that plot — examine it.`
            attachAnnotation({ image: b64, note })
          }
        }
      } catch { /* not fetchable (remote/CORS) — prefill only */ }
    }
    const prefill = action === 'revision-supersede'
      ? `Make a revision of "${label}"${idClause} (superseding any newer revisions — confirmed). Change: `
      : action === 'revision'
      ? `Make a revision of "${label}"${idClause} with the following change: `
      : action === 'reproduce'
      ? `Reproduce "${label}"${idClause}. Re-run the exec in the current environment and report any drift.`
      : annotation
        ? `Look at "${label}" and highlighting. `
        : `Let's look at "${label}". `
    setPrefill(prefill)
    setComposerFocus(n => n + 1)
  }

  // Pin a run output (used by the detached preview window, which carries the
  // run id since it isn't mounted inside that run's view).
  const pinRunOutput = async (runId: string | undefined, item: { kind?: string; label: string; thumb?: string; href?: string; size?: string }) => {
    if (!runId) return
    await fetch(`/api/runs/${encodeURIComponent(runId)}/pin-output`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: item.kind ?? 'figure', label: item.label, thumb: item.thumb, href: item.href, size: item.size }),
    }).catch(() => {})
    refresh()
  }

  // Detached preview windows post pin/chat/highlight requests back to us via
  // window.opener.postMessage. Keep the handler in a ref so the listener is
  // registered once but always calls the latest closures.
  const previewMsgRef = useRef<(m: { type: string; runId?: string; item: { kind?: string; label: string; thumb?: string; href?: string; size?: string }; annotation?: { image: string; note: string } }) => void>(() => {})
  previewMsgRef.current = (m) => {
    if (m.type === 'pin') pinRunOutput(m.runId, m.item)
    else if (m.type === 'chat') chatAboutResult(m.item.label, m.item.thumb)
    else if (m.type === 'chat-annot' && m.annotation) chatAboutResult(m.item.label, undefined, m.annotation)
  }
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return
      const d = e.data
      if (d && typeof d === 'object' && d.__abaPreview) previewMsgRef.current(d)
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [])

  // Conclude → make a claim. Creating is a deliberate open: we focus the new
  // claim entity-first so the user reviews/edits the draft (consistent with the
  // state scheme — create = open, not a single-click preview).
  const createClaim = async (statement: string, evidence_ids: string[]) => {
    const r = await fetch('/api/claims', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ statement, evidence_ids, thread_id: currentThread?.id ?? threadId }),
    })
    if (r.ok) { const c = await r.json(); refresh(); openEntity(c.id) }
  }
  // Claim from a kept Result: seed the statement from its interpretation.
  const claimFromResult = (resultId: string) => {
    const r = entities.find(e => e.id === resultId)
    const stmt = (r?.metadata?.interpretation as string) || r?.title || ''
    createClaim(stmt, [resultId])
  }

  // Pin/unpin a chat figure. Under the unified model (misc/entity_pin_redesign.md),
  // Pin creates a Result wrapping the evidence (and lands on the shelf by virtue
  // of being a Result); Unpin runs the inverse branching (delete vs archive vs
  // remove-member) via /api/entities/{id}/unpin.
  const pinEntity = (id: string, pin: boolean) => {
    const path = pin ? 'pin' : 'unpin'
    fetch(`/api/entities/${encodeURIComponent(id)}/${path}`, { method: 'POST' })
      .then(() => refresh())
      .catch(() => {})
    // User-initiated pin/unpin reveals the right rail — the result they're
    // pinning lands there, so they want to see it. (AI-driven pinning, e.g.
    // auto_interpret's caption write-back, goes through a different code path
    // and intentionally does NOT call this — see the right-rail effect above.)
    // User-initiated pin/unpin is intentional — clear the sticky and reveal.
    if (pin) {
      userCollapsedRef.current = false; _setRightCollapsedRaw(false)
      // The live path for the autogen title/caption is auto_interpret's
      // caption-ready broadcast on /api/notifications → refresh(). But a
      // single SSE event has no replay: if it's missed (EventSource mid-
      // reconnect, a --reload worker restart, a slow-client queue drop) the
      // UI sits on the placeholder until a manual reload. A few bounded
      // refreshes cover that gap (auto_interpret typically lands in ~6s);
      // refresh() is idempotent so overlapping with the broadcast is free.
      ;[3000, 7000, 12000].forEach(ms => window.setTimeout(() => refresh(), ms))
    }
  }
  // Keep any (non-entity) message as a snapshot note, keyed by content.
  const keepMessage = (key: string, text: string, image_urls: string[]) => {
    fetch('/api/messages/pin', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, text, image_urls, thread_id: currentThread?.id ?? threadId }),
    }).then(() => refresh()).catch(() => {})
  }
  // Keys of currently-kept message notes (to reflect pin state in chat).
  // Only active notes count — an archived (unpinned) note must not keep the
  // chat pin button lit.
  const keptKeys = new Set(
    entities
      .filter(e => e.type === 'note' && e.status === 'active' && (e.metadata?.source_key as string))
      .map(e => e.metadata!.source_key as string),
  )

  // Figures kept by an active Result — derives chat figure pin state on
  // re-entry. (Task #318 dropped the `entity.pinned` flag in favor of
  // membership: a figure is "pinned" iff it appears as a member of an
  // active Result.)
  const pinnedFigureIds = new Set<string>(
    entities
      .filter(e => e.type === 'result' && e.status === 'active')
      .flatMap(e => ((e.metadata?.members as Array<{kind?: string, ref?: string}>) ?? [])
        .filter(m => m.kind === 'figure' && m.ref)
        .map(m => m.ref as string)),
  )

  if (view === 'home') {
    return (
      <div className="app app--home">
        <Rail onEntitiesChanged={refresh} view={view} onNavigate={goToView} />
        <Home onEnter={enterProject} onProjectsChanged={refreshCurrent} />
      </div>
    )
  }

  const gridCols = `var(--w-rail) ${treeCollapsed ? 0 : treeW}px ${treeCollapsed ? 8 : 10}px 1fr`
  const sectionCounts = {
    threads: 1 + activeEntities.filter(e => e.type === 'thread' && !e.metadata?.is_default).length,
    claims: activeEntities.filter(e => e.type === 'claim').length,
    data: activeEntities.filter(e => e.type === 'dataset').length,
    // Exclude the ambient catch-all analysis (lifecycle/registry.py:_ensure_analysis)
    // — structural bookkeeping, never user-facing.
    runs: activeEntities.filter(e => e.type === 'analysis' && !(e.metadata as { ambient?: boolean } | undefined)?.ambient).length,
    // Aligned with PinnedShelf + ProjectTree's Results tab: only Result entities
    // count as "Results" — figures/tables live as evidence under Runs/Results.
    results: activeEntities.filter(e => e.type === 'result').length,
    // Virtual files view shows the same artifacts as Results but via a folder
    // tree projection — count = same as results for now.
    files: activeEntities.filter(e => ['figure', 'table', 'result', 'note', 'narrative'].includes(e.type) && e.artifact_path).length,
  }
  const openProjectSection = (section: ProjectSection) => {
    setProjectSection(section)
    if (treeCollapsed) setTreeCollapsed(false)
  }

  const chatPane = (compact: boolean) => (
    <ChatPane
      messages={messages}
      streaming={streaming}
      loading={chatLoading}
      streamMsg={streamMsg}
      onSend={(text: string) => streaming ? enqueue(text) : sendMessage(text)}
      focusedEntity={focused}
      annotation={annotation}
      onClearAnnotation={clearAnnotation}
      prefill={prefill}
      onPrefillConsumed={() => setPrefill('')}
      composerFocus={composerFocus}
      onAnnotate={attachAnnotation}
      annotClear={annotClear}
      onRetry={retryLast}
      embedded
      compact={compact}
      entities={entities}
      onPin={pinEntity}
      onArtifactPinned={() => {
        // Option B / Phase 3: same side effects as pinEntity (refresh +
        // reveal the right rail) but no server call — the
        // /api/artifacts/.../pin POST already happened inside ArtifactPin.
        refresh()
        userCollapsedRef.current = false
        _setRightCollapsedRaw(false)
      }}
      pinnedFigureIds={pinnedFigureIds}
      keptKeys={keptKeys}
      onKeepMessage={(key, text, image_urls) => keepMessage(key, text, image_urls)}
      onClaimFromSelection={text => createClaim(text, [])}
      highlighting={compact ? undefined : highlighting}
      onHighlightingChange={compact ? undefined : setHighlighting}
      starters={compact ? undefined : (currentThread?.metadata?.orient_steps as string[] | undefined)}
      pendingClarification={pendingClarification}
      onAnswerClarification={answerClarification}
      pendingApproval={pendingApproval}
      onRespondApproval={respondApproval}
      onStop={stopTurn}
      queuedMessage={queuedMessage}
      onDropQueue={dropQueue}
      onSteer={steer}
      threadId={currentThread?.id ?? threadId}
      currentRunId={currentRunId}
    />
  )

  const entityPanel = (primary: boolean) => (
    <div className={`surface-panel entity-surface ${primary ? 'primary' : ''}`}>
      {viewedFile ? (
        <FileCanvas
          node={viewedFile}
          onFocus={goToEntity}
          onClose={() => setViewedFile(null)}
        />
      ) : (
        <FocusCanvas
          entity={focused}
          entities={entities}
          onChange={refresh}
          onFocus={goToEntity}
          onSelectThread={selectThread}
          onAnnotate={attachAnnotation}
          annotClear={annotClear}
          compact={!primary}
          onAsk={askGuide}
          onChatResult={chatAboutResult}
          onBrowseFiles={(path?: string) => { openProjectSection('files'); setFilesTarget(t => ({ path: path ?? '', n: t.n + 1 })) }}
          projectId={url.pid ?? undefined}
          highlighting={highlighting}
          onHighlightingChange={setHighlighting}
        />
      )}
    </div>
  )

  // Chat-first right peek (when not scoped to a specific artifact): the current
  // thread's pinned shelf.
  const peekShelf = (
    <PinnedShelf
      entities={entities}
      threadId={currentThread?.id ?? null}
      threads={entities.filter(e => e.type === 'thread' && e.status !== 'archived')}
      onChange={refresh}
      onFocus={goToEntity}
      onClaimFrom={claimFromResult}
    />
  )

  // ⓘ — opens the Manifest drawer. Rendered inside the right column so
  // it sticks above the column's top-right corner (chat-first: above the
  // Questions panel; entity-first: above the chat peek).
  const drawerToggle = (
    <button
      className={`drawer-fab ${drawerOpen ? 'is-open' : ''}`}
      onClick={() => setDrawerOpen(o => !o)}
      title="Show what the agent is seeing this turn"
    >
      ⓘ
    </button>
  )

  return (
    <div className="app app--workspace" style={{ gridTemplateColumns: gridCols }}>
      <Rail
        onEntitiesChanged={refresh}
        view={view}
        onNavigate={goToView}
        collapsed={treeCollapsed}
        projectTitle={projectName}
        sectionCounts={sectionCounts}
        activeSection={projectSection}
        onProjectSection={openProjectSection}
      />
      {treeCollapsed ? <div /> : (
        <ProjectTree
          entities={entities}
          focusedId={focusedId}
          activeSection={projectSection}
          onFocus={goToEntity}
          onViewFile={viewFile}
          onChange={refresh}
          currentThread={threadId}
          onSelectThread={selectThread}
          onOpenOverview={() => setOverview(true)}
          onOpenThreadOverview={openThreadOverview}
          filesTarget={filesTarget}
          projectId={url.pid ?? undefined}
        />
      )}
      <HResizer
        collapsed={treeCollapsed}
        onDrag={dx => setTreeW(w => Math.min(440, Math.max(TREE_MIN, w + dx)))}
        onToggle={() => setTreeCollapsed(c => !c)}
      />

      <div className="canvas">
        <div className="canvas-head">
          <div className="canvas-title">
            {overview ? (
              <>
                <button className="canvas-back" onClick={() => setOverview(false)} title="Back to the workspace">
                  ← {projectName}
                </button>
                <span className="canvas-title__type">overview</span>Project overview
              </>
            ) : inventory && currentThread ? (
              <>
                <button className="canvas-back" onClick={() => setInventory(false)} title="Back to the conversation">
                  ← {currentThread.title}
                </button>
                <span className="canvas-title__type">overview</span>Thread overview
              </>
            ) : (<>
            {posture === 'entity' && scoped && (
              <button className="canvas-back" onClick={backToThread} title="Back to the thread conversation">
                ← {currentThread?.title ?? 'thread'}
              </button>
            )}
            {scoped
              ? <><span className="canvas-title__type">{typeLabel(focused!.type)}</span>{focused!.title}</>
              : currentThread
              ? <EditableThreadTitle thread={currentThread} onRenamed={refresh} />
              : <>{projectName}</>}
            </>)}
          </div>
          <div className="canvas-actions">
            {!overview && !inventory && (posture === 'chat' || focused?.type === 'result') && (
              <button
                className={`canvas-hl ${highlighting ? 'is-on' : ''}`}
                onClick={() => setHighlighting(v => !v)}
                title={highlighting
                  ? 'Cancel highlight'
                  : (focused?.type === 'result'
                     ? 'Highlight a region of any panel (figure, caption, or note) to ask Guide about it'
                     : 'Highlight a region of any message to ask Guide about it')}
              >
                <svg viewBox="0 0 24 24" width="13" height="13" fill="#fde047" stroke="#a16207" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M15.6 2.6a2 2 0 012.8 0l3 3a2 2 0 010 2.8l-9 9-5.2 1.2 1.2-5.2 9-9zM5 19h14v2H5z"/></svg>
                Highlight
              </button>
            )}
            {!overview && currentThread && !inventory && (
              <button className="canvas-inventory" title="Thread overview — all items by status"
                onClick={() => setInventory(true)}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"><rect x="3" y="4" width="5" height="16" rx="1"/><rect x="9.5" y="4" width="5" height="16" rx="1"/><rect x="16" y="4" width="5" height="16" rx="1"/></svg>
                overview
              </button>
            )}
            <AdvisorStrip focusedId={focusedId} focusedType={focused?.type}
                          onTry={setPrefill} onFocus={goToEntity} />
            {!overview && <PostureToggle posture={posture} onChange={setPosture} entityLabel={entityLabel(focused)} />}
          </div>
        </div>

        <div className="canvas-body">
          {overview ? (
            <ProjectOverview
              entities={entities}
              onGoTo={goToEntity}
              onSelectThread={(id) => { setOverview(false); selectThread(id) }}
              onClose={() => setOverview(false)}
              onChange={refresh}
              onAsk={askGuide}
            />
          ) : inventory && currentThread ? (
            <ThreadOverview
              entities={entities}
              thread={currentThread}
              threadId={currentThread.id}
              onGoTo={goToEntity}
              onSelectThread={selectThread}
              onChange={refresh}
              onAsk={askGuide}
            />
          ) : (
          <div className={`split split--${posture} ${rightCollapsed ? 'split--right-collapsed' : ''}`}
               style={{ gridTemplateColumns: rightCollapsed ? '1fr 10px 0' : `1fr 10px ${rightW}px`, gap: 0 }}>
            {/* Console/context (ⓘ) pinned to the split's top-right — i.e. the
                top-right of the rightmost column: the right column when expanded,
                the chat when it's collapsed. Sticks out of the corner. */}
            {drawerToggle}
            {posture === 'chat' ? (
              <>
                <div className="surface-panel primary chat-primary">{chatPane(false)}</div>
                <HResizer
                  side="right"
                  collapsed={rightCollapsed}
                  onDrag={dx => setRightW(w => Math.min(RIGHT_MAX, Math.max(RIGHT_MIN, w - dx)))}
                  onToggle={userToggleRail}
                />
                <div className="thread-context">
                  {currentThread && (
                    <ThreadHeader thread={currentThread} onChange={refresh} onSwitchThread={selectThread}
                                  onOpenFull={() => openEntity(currentThread.id)} />
                  )}
                  {proposals.length > 0 && (
                    <div className="proposals">
                      {proposals.map(p => (
                        <ProposalCard key={p.id} p={p} onAccept={acceptProposal} onDismiss={dismissProposal} />
                      ))}
                    </div>
                  )}
                  {peekShelf}
                </div>
              </>
            ) : (
              <>
                {entityPanel(true)}
                <HResizer
                  side="right"
                  collapsed={rightCollapsed}
                  onDrag={dx => setRightW(w => Math.min(RIGHT_MAX, Math.max(RIGHT_MIN, w - dx)))}
                  onToggle={userToggleRail}
                />
                <div className="chat-peek-anchor">
                  <div className="surface-panel chat-peek">{chatPane(true)}</div>
                </div>
              </>
            )}
          </div>
          )}
        </div>
      </div>

      <SearchModal open={searchOpen} onClose={() => setSearchOpen(false)}
                   onPick={openEntity} />
      <UndoToast undoable={undoable} onUndo={undoProposal} onClose={clearUndo} />

      {/* T2.4 Drawer: slides in from the right when the toggle is clicked.
          The toggle (ⓘ) lives inside the right column so it floats above
          that column's top-right corner. */}
      {drawerOpen && (
        <div className="drawer-overlay">
          <Drawer
            manifest={manifest}
            focusEntityId={focusedId}
            threadId={threadId === 'default' ? (currentThread?.id ?? null) : threadId}
            eventLog={eventLog}
            jobs={jobs}
            onClose={() => setDrawerOpen(false)}
          />
        </div>
      )}
    </div>
  )
}
