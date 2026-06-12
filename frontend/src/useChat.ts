import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { DisplayMessage, Block, SSEEvent, ManifestSnapshot, PendingClarification, PendingApproval, LogEntry, JobInfo } from './types'
// W2-#4 phase 2: the SSE reader loop lives in a small reusable helper.
// Pulls ~50 LOC out of runStream and makes the terminal-event / premature-
// close / cancellation behavior unit-testable in isolation.
import { readSSEStream } from './lib/sseReader'

type RawMsg = { role: string; content: unknown[]; ts?: string }

/** Coerce a model-supplied plan `steps` value to a clean string[] — the model
 *  sometimes returns a single string (or other shape) instead of an array. */
function asSteps(x: unknown): string[] {
  if (Array.isArray(x)) return x.map(s => String(s)).filter(Boolean)
  if (typeof x === 'string' && x.trim()) {
    return x.split('\n').map(s => s.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, '').trim()).filter(Boolean)
  }
  return []
}

function blocksFromContent(content: Record<string, unknown>[]): Block[] {
  const blocks: Block[] = []
  for (const block of content) {
    if (block.type === 'text') {
      blocks.push({ type: 'text', text: block.text as string })
    } else if (block.type === 'tool_use') {
      if (block.name === 'present_plan') {
        const inp = (block.input ?? {}) as { title?: string; steps?: unknown; rationale?: string }
        // History rehydration: steps may be a list of strings (older
        // turns) or PlanStepShape objects (T2.5+); forward as-is so the
        // renderer can inspect.
        blocks.push({
          type: 'plan',
          title: inp.title,
          summary: (inp as Record<string, unknown>).summary as string | undefined,
          rationale: inp.rationale,
          assumptions: (inp as Record<string, unknown>).assumptions as string[] | undefined,
          steps: (Array.isArray(inp.steps) ? inp.steps : asSteps(inp.steps)) as (string | import('./types').PlanStepShape)[],
        })
      } else {
        blocks.push({
          type: 'tool_start',
          name: block.name as string,
          input: (block.input ?? {}) as Record<string, unknown>,
        })
      }
    } else if (block.type === 'tool_result') {
      // present_plan / ask_clarification: their UI is already rendered by the
      // plan card / mini-composer, so we drop the tool_result block.
      // Orphan-fill (server / kernel crashed mid-turn): PUSH a synthetic
      // error-shaped result so the prior assistant's tool_use resolves out
      // of its spinner state — earlier we `continue`d here, which left the
      // tool_start spinning forever on reload (PK 2026-06-02, thr_705b6af3).
      const raw = block.content
      const isLegacyOrphan = typeof raw === 'string' && raw.startsWith('[tool result unavailable')
      if (isLegacyOrphan) {
        blocks.push({ type: 'tool_result', name: '(interrupted)',
                      result: { error: 'tool did not complete (interrupted)' } })
        continue
      }
      // Multimodal tool results (e.g. view_artifact) carry an ARRAY of
      // Anthropic content blocks ([{type:'text'},{type:'image'}]) rather than
      // a JSON string. JSON.parse(array) coerces it to "[object Object],…"
      // and throws, so the catch below used to render that literal string in
      // chat. Handle the array shape explicitly: surface the text preamble as
      // the result and render any image blocks inline (base64 → data URL).
      if (Array.isArray(raw)) {
        const arr = raw as Array<Record<string, unknown>>
        const txt = arr
          .filter(x => x && x.type === 'text' && typeof x.text === 'string')
          .map(x => x.text as string).join('\n').trim()
        blocks.push({ type: 'tool_result', name: '(result)',
                      result: txt ? { stdout: txt } : {} })
        for (const x of arr) {
          if (!x || x.type !== 'image') continue
          const src = x.source as { type?: string; media_type?: string; data?: string } | undefined
          if (src && src.type === 'base64' && src.data) {
            blocks.push({ type: 'image',
                          url: `data:${src.media_type || 'image/png'};base64,${src.data}`,
                          alt: 'viewed artifact' })
          }
        }
        continue
      }
      try {
        const parsed = JSON.parse(raw as string)
        if (parsed && parsed.status === 'interrupted') {
          blocks.push({ type: 'tool_result', name: '(interrupted)',
                        result: { error: parsed.note || 'tool did not complete (interrupted)' } })
          continue
        }
        if (parsed && parsed.status === 'presented') continue     // present_plan ack — the plan card already shows it
        if (parsed && parsed.status === 'asked') continue         // ask_clarification ack — the mini-composer already shows it
        blocks.push({ type: 'tool_result', name: '(result)', result: parsed })
        if (parsed.plots && Array.isArray(parsed.plots)) {
          const _exec_id = (typeof parsed.exec_id === 'string') ? parsed.exec_id : undefined
          for (let i = 0; i < parsed.plots.length; i++) {
            const p = parsed.plots[i]
            blocks.push({
              type: 'image', url: p.url, alt: p.original_name,
              // Stage 1 / Option B Phase 3: tag the inline image with its
              // canonical artifact id so chat-level pin works without a
              // pre-materialized entity. exec_id comes from run_python's
              // post-harvest write of the execution_records row.
              artifact_id: _exec_id ? `${_exec_id}:figure:${i}` : undefined,
              // PDF (and any future non-raster figure format) — backend
              // annotates with the rasterized .preview.png so chat can
              // <img src> something a browser actually renders.
              preview_url: p.preview_url,
            })
          }
        }
      } catch {
        blocks.push({ type: 'text', text: String(raw) })
      }
    }
  }
  return blocks
}

// Collapse Anthropic-style turn structure (assistant_text+tool_use, then a
// user message carrying tool_results) into a single display message so the
// historical view matches the live-streamed view.
function collapseHistory(raw: RawMsg[]): DisplayMessage[] {
  const display: DisplayMessage[] = []
  let i = 0
  while (i < raw.length) {
    const m = raw[i]
    if (m.role === 'user') {
      const allToolResults = (m.content as Record<string, unknown>[]).every(
        b => b.type === 'tool_result',
      )
      if (allToolResults) {
        // Lift these tool_results into the previous assistant message's blocks.
        const last = display[display.length - 1]
        if (last && last.role === 'assistant') {
          last.blocks = [...last.blocks, ...blocksFromContent(m.content as Record<string, unknown>[])]
        }
        i++
        continue
      }
    }
    display.push({
      id: `hist-${i}`,
      role: m.role as 'user' | 'assistant',
      blocks: blocksFromContent(m.content as Record<string, unknown>[]),
      ts: m.ts,
    })
    i++
  }
  return display
}

interface Annotation { image: string; note: string }

// Observability Console: map an SSE event to a log entry (or null to skip —
// `delta` is the chat text, not worth logging). `level` gates it in the
// detail-level selector (1=progress, 2=tools, 3=debug).
function _summInput(o: Record<string, unknown>): string {
  try {
    return Object.entries(o || {}).map(([k, v]) => `${k}=${String(v).slice(0, 30)}`).join(' ').slice(0, 90)
  } catch { return '' }
}
function logFor(ev: SSEEvent): LogEntry | null {
  const t = Date.now()
  switch (ev.type) {
    case 'delta': return null
    case 'tool_progress': return { t, type: ev.type, label: ev.message, level: 1 }
    case 'tool_chunk':    return { t, type: ev.type, label: `${ev.stream}+${ev.text.length}B (${(ev.bytes_total/1024).toFixed(1)}KB total)`, level: 3 }
    case 'plan': return { t, type: ev.type, label: ev.title || 'plan', level: 1 }
    case 'notice': return { t, type: ev.type, label: ev.text, level: 1 }
    case 'error': return { t, type: ev.type, label: ev.text, level: 1 }
    case 'cancelled': return { t, type: ev.type, label: ev.reason || 'cancelled', level: 1 }
    case 'done': return { t, type: ev.type, label: 'turn done', level: 1 }
    case 'tool_start': return { t, type: ev.type, label: `${ev.name} ${_summInput(ev.input)}`, level: 2 }
    case 'tool_result': {
      const st = (ev.result as Record<string, unknown>)?.status
      return { t, type: ev.type, label: ev.name + (st ? ` · ${st}` : ''), level: 2 }
    }
    case 'job_submitted': return { t, type: ev.type, label: `job ${ev.job.id} ${ev.job.status || ''}`, level: 2 }
    case 'entity_registered': return { t, type: ev.type, label: `${ev.entity.type}: ${ev.entity.title}`, level: 2 }
    case 'clarification_pending': return { t, type: ev.type, label: ev.question, level: 2 }
    case 'approval_pending': return { t, type: ev.type, label: `approve ${ev.tool_name}`, level: 2 }
    case 'deferred_tool_pending': return { t, type: ev.type, label: `${ev.tool_name} → queued (${ev.deferred_id})`, level: 2 }
    case 'manifest': return { t, type: ev.type, label: `turn ${ev.manifest.turn_index}`, level: 3 }
    default: return { t, type: (ev as { type: string }).type, label: '', level: 3 }
  }
}

export function useChat(
  focusEntityId: string,
  onEntityRegistered?: () => void,
  annotation?: Annotation | null,
  reloadKey?: unknown,
  threadId: string = 'default',
  projectId?: string,
) {
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  // Ref-shadow of `streaming` so the Stop button's setTimeout-retry can
  // read the latest value (state closure would freeze at click time).
  const streamingRef = useRef(false)
  useEffect(() => { streamingRef.current = streaming }, [streaming])
  const [loading, setLoading] = useState(false)   // fetching a thread's history
  const [streamMsg, setStreamMsg] = useState<DisplayMessage | null>(null)
  const [manifest, setManifest] = useState<ManifestSnapshot | null>(null)
  // Observability panel: a bounded tail of SSE events (Console tab) and the
  // last-known state of background jobs (Jobs tab). Client-side views over the
  // stream we already consume — no extra server cost.
  const [eventLog, setEventLog] = useState<LogEntry[]>([])
  const [jobs, setJobs] = useState<JobInfo[]>([])
  // Phase A — poll /api/jobs every 5s to keep the (i) drawer's Jobs tab
  // honest. Before this, jobs were only mutated by SSE 'job_submitted'
  // events during a live turn — so as soon as the turn closed, the tab
  // froze on 'queued' even after the worker finished the job (live bug
  // 2026-06-05). 5s cadence is plenty for a status badge; the response
  // is small and the round-trip is cheap.
  //
  // Side effect: this poll ALSO drives the continuation-attach probe.
  // When a Phase-C continuation fires server-side, a NEW run starts on
  // the originating thread without the browser knowing about it (it
  // wasn't initiated via /api/chat). We watch /active-turn alongside;
  // if a new run_id appears while we're idle, we attach to its SSE
  // stream so the continuation message + the agent's response show up
  // live instead of requiring a page reload (live bug 2026-06-05,
  // same session).
  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const r = await fetch('/api/jobs')
        if (!r.ok || cancelled) return
        const fresh = await r.json() as Array<{ id: string; status: string; title?: string; created_at?: string }>
        if (cancelled) return
        setJobs(prev => {
          const byId = new Map(prev.map(j => [j.id, j]))
          for (const j of fresh) {
            const existing = byId.get(j.id)
            byId.set(j.id, {
              id: j.id,
              status: j.status || 'queued',
              title: j.title,
              t: existing?.t ?? (j.created_at ? Date.parse(j.created_at) : Date.now()),
            })
          }
          return Array.from(byId.values())
        })
      } catch (_) { /* swallow */ }
      // Continuation-attach probe — runs in the same tick so we don't
      // double our background traffic. Only fires when we're NOT already
      // streaming our own turn, and only when a DIFFERENT run_id is live.
      if (cancelled || streamingRef.current || !threadId) return
      try {
        const ar = await fetch(
          `/api/threads/${encodeURIComponent(threadId)}/active-turn${
            projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`)
        if (!ar.ok || cancelled) return
        const row = await ar.json()
        if (!row || !row.run_id) return
        if (row.run_id === lastRunIdRef.current) return
        // New active turn detected (a continuation fired, or a peer turn
        // started). Refresh persisted history so any already-landed
        // synthetic '[continuation:]' user message is in the visible
        // log, then reattach to the live SSE stream.
        lastRunIdRef.current = row.run_id
        lastSeqRef.current = 0
        await loadMessages()
        runStreamRef.current?.({ reattachRunId: row.run_id, since: 0 })
      } catch (_) { /* probe is best-effort */ }
    }
    tick()
    const h = window.setInterval(tick, 5000)
    return () => { cancelled = true; window.clearInterval(h) }
    // threadId + projectId in deps so a thread/project switch tears down
    // the old probe and starts a fresh one against the new ids.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, projectId])
  // B1 — when the Guide pauses on ask_clarification, the UI shows an
  // inline mini-composer. Cleared when the resume turn starts streaming.
  const [pendingClarification, setPendingClarification] = useState<PendingClarification | null>(null)
  // P1 #3 — when a flagged tool needs user approval before running. By
  // design rare; the bar should be "real money / hard-to-reverse only".
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null)
  // Track the currently-streaming run_id so the Stop button can target
  // the right turn. Cleared when the stream ends (done/error/cancelled).
  const currentRunIdRef = useRef<string | null>(null)
  // Queue-while-streaming: user can type + commit a follow-up while the
  // agent is responding. Auto-flushes when the current turn ends (done
  // OR cancelled via Steer). Stop drops the queue.
  const [queuedMessage, setQueuedMessage] = useState<string | null>(null)
  // Mirror of queuedMessage in a ref. The in-flight stream's done/cancelled
  // handlers capture `queuedMessage` from the closure at TURN START (when it was
  // null), so reading the state there is stale — that's why "Send now" / auto-flush
  // dropped a message queued mid-turn. The ref always holds the latest queue.
  const queuedMessageRef = useRef<string | null>(null)
  // A flag distinguishing Steer (cancel → flush queue) from Stop
  // (cancel → drop queue). Set by steer(); read in the 'cancelled'
  // handler; cleared either way.
  const steerFlushRef = useRef(false)
  // sendMessage is declared later in the hook; the SSE handler closure
  // captures this ref so it can fire a new turn when the current one
  // finishes (auto-flush of queued message).
  const sendMessageRef = useRef<((text: string) => Promise<void>) | null>(null)
  // Unguarded sender for the auto-flush / steer paths. The turn has JUST ended,
  // so sendMessage's `if (streaming) return` guard — whose stale `streaming`
  // closure still reads true at the setTimeout(0) instant — would silently drop
  // the queued message (chip clears, nothing sends). This bypasses that guard.
  const flushSendRef = useRef<((text: string) => void) | null>(null)
  const onERRef = useRef(onEntityRegistered)
  onERRef.current = onEntityRegistered
  const annotationRef = useRef(annotation)
  annotationRef.current = annotation
  // Each thread/project switch bumps the generation; any in-flight stream or
  // message load tagged with an older generation bails so it can't leak the old
  // thread's content into the new one.
  const genRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)
  // C-1: last seq we've seen on the current stream. Persisted to
  // localStorage per thread alongside the run_id so a hard reload can
  // reattach with `?since=<lastSeq>` — but only if the in-flight Turn's
  // run_id matches the one we last saw (seqs are per-run, so a stale
  // entry from a completed Turn must not be used against a fresh one).
  const lastSeqRef = useRef(0)
  const lastRunIdRef = useRef<string | null>(null)
  const lastSeqKey = (tid: string) => `aba:lastSeq:${tid}`
  const readPersistedSeq = (tid: string): { runId: string | null; seq: number } => {
    try {
      const raw = localStorage.getItem(lastSeqKey(tid))
      if (!raw) return { runId: null, seq: 0 }
      const obj = JSON.parse(raw)
      return { runId: typeof obj.runId === 'string' ? obj.runId : null,
               seq: Number.isFinite(obj.seq) ? obj.seq : 0 }
    } catch { return { runId: null, seq: 0 } }
  }
  const writePersistedSeq = (tid: string, runId: string, seq: number) => {
    try { localStorage.setItem(lastSeqKey(tid), JSON.stringify({ runId, seq })) }
    catch { /* private mode / quota — non-fatal */ }
  }

  // Load the current thread's persisted conversation (ignored if superseded).
  // C-1: after history loads, probes /active-turn — if a Turn is still in
  // flight on this thread, reattach via /api/turns/{rid}/stream?since=
  // <lastSeq>. The agent loop runs as a background task on the server and
  // survives client disconnect, so we get the live stream back including
  // any events emitted while we were away.
  const loadMessages = useCallback(async () => {
    const myGen = genRef.current
    try {
      // project_id pinned per-request — without it the backend races with
      // /api/projects/{pid}/open and may read from the prior project's DB,
      // returning [] (first load after server bounce shows empty chat,
      // refresh fixes it). PK 2026-06-03.
      const pq = projectId ? `&project_id=${encodeURIComponent(projectId)}` : ''
      const r = await fetch(`/api/messages?thread_id=${encodeURIComponent(threadId)}${pq}`)
      const raw = (await r.json()) as RawMsg[]
      // Skip the overwrite if a send started AFTER this fetch was
      // launched — the optimistic user bubble is the source of truth
      // until the SSE finalizes it. (Race fix, 2026-06-12: see
      // sendMessage for the matching synchronous streamingRef flip.)
      if (r.ok && genRef.current === myGen && !streamingRef.current) {
        setMessages(collapseHistory(raw))
      }
    } catch { /* ignore */ }
    finally { if (genRef.current === myGen) setLoading(false) }
    if (streamingRef.current) return    // already streaming via our own POST
    try {
      const ar = await fetch(`/api/threads/${encodeURIComponent(threadId)}/active-turn${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`)
      if (!ar.ok || genRef.current !== myGen) return
      const row = await ar.json()
      if (!row || !row.run_id || genRef.current !== myGen) return
      // Real reattach — open the sink stream for this Turn. Use the
      // persisted seq ONLY if it was for THIS run_id; otherwise start
      // from 0 (replay the in-memory tail). runStreamRef is set just
      // below to avoid a temporal dead zone (runStream uses
      // loadMessages indirectly).
      const since = (lastRunIdRef.current === row.run_id) ? lastSeqRef.current : 0
      if (since === 0) {
        // Stale persisted entry — reset so the new run can update it.
        lastSeqRef.current = 0
        lastRunIdRef.current = row.run_id
      }
      runStreamRef.current?.({ reattachRunId: row.run_id, since })
    } catch { /* probe is best-effort */ }
    // deps MUST include projectId — without it, the closure captures whatever
    // projectId was at first mount (often undefined briefly, or stale from a
    // prior project), and the &project_id=... pin is wrong → backend reads
    // from the wrong DB → user sees another project's chat (PK 2026-06-03
    // observed: opening test0 showed Sp9's chat). The useEffect that calls
    // loadMessages depends on loadMessages's identity, so re-deriving the
    // callback on projectId change also re-fires the fetch.
  }, [threadId, projectId])
  // Ref-shadow of runStream so loadMessages can call it without a TDZ
  // (runStream is declared after loadMessages and depends on it).
  const runStreamRef = useRef<((opts: { text?: string; retry?: boolean; annotation?: Annotation | null; resumeRunId?: string; approvalAction?: 'approve' | 'approve_session' | 'reject'; reattachRunId?: string; since?: number }) => Promise<void>) | null>(null)

  // On a project switch (reloadKey) or thread switch (threadId): reset
  // SYNCHRONOUSLY (before paint) so the chat pane never shows the previous
  // thread's content for even a frame — it tracks the rail instantly.
  useLayoutEffect(() => {
    genRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    setStreamMsg(null)
    setStreaming(false)
    setMessages([])
    setLoading(true)
    currentRunIdRef.current = null
    // C-1: seed lastSeq + run_id from localStorage for the new thread.
    // Used by the active-turn probe below — only applied if the live
    // run_id matches what we last persisted.
    const persisted = readPersistedSeq(threadId)
    lastSeqRef.current = persisted.seq
    lastRunIdRef.current = persisted.runId
  }, [reloadKey, threadId])

  // Then fetch the new thread's conversation (after paint).
  useEffect(() => { loadMessages() }, [reloadKey, threadId, loadMessages])

  // Shared streaming core. Three modes:
  //  - default: post `text` as a fresh chat turn (POST /api/chat).
  //  - retry: regenerate the last turn server-side (no new user message).
  //  - resumeRunId: the user is answering a paused AWAITING_USER turn
  //    (ask_clarification, plan Go/Adjust); posts to
  //    /api/turns/{runId}/resume, which inherits thread+focus from the
  //    prior turn and drives a fresh Turn forward.
  const runStream = useCallback(
    async (opts: { text?: string; retry?: boolean; annotation?: Annotation | null; resumeRunId?: string; approvalAction?: 'approve' | 'approve_session' | 'reject'; reattachRunId?: string; since?: number }) => {
      const myGen = genRef.current
      const ac = new AbortController()
      abortRef.current = ac
      setStreaming(true)
      // A resume implicitly accepts whatever pending question/plan we were on.
      if (opts.resumeRunId) { setPendingClarification(null); setPendingApproval(null) }
      const assistantId = `a-${Date.now()}`
      const streamingBlocks: Block[] = []
      setStreamMsg({ id: assistantId, role: 'assistant', blocks: [] })
      const live = () => genRef.current === myGen   // false once the thread switched

      // Sticky: the marked region stays attached across follow-up messages
      // so the agent retains it; the user clears it explicitly via the chip.
      // An explicit per-call annotation (e.g. "chat about this plot") wins.
      const annot = opts.annotation !== undefined ? opts.annotation : annotationRef.current

      try {
        // C-1: three modes — reattach (GET /api/turns/{rid}/stream) for
        // resuming an in-flight Turn after disconnect/reload, resume
        // (POST /api/turns/{rid}/resume) for the user's reply to a paused
        // AWAITING_USER turn, or fresh chat (POST /api/chat).
        // project_id pinned per-request — never trust the backend's global
        // "current project" state (it gets clobbered by bounces / multi-tab /
        // side-scripts and silently misroutes writes to the wrong project DB).
        const pidQ = projectId ? `&project_id=${encodeURIComponent(projectId)}` : ''
        const res = opts.reattachRunId
          ? await fetch(`/api/turns/${encodeURIComponent(opts.reattachRunId)}/stream?since=${opts.since ?? 0}${pidQ}`, {
              method: 'GET',
              signal: ac.signal,
            })
          : opts.resumeRunId
          ? await fetch(`/api/turns/${encodeURIComponent(opts.resumeRunId)}/resume`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              signal: ac.signal,
              body: JSON.stringify({
                user_text: opts.text ?? '',
                ...(opts.approvalAction ? { action: opts.approvalAction } : {}),
                ...(projectId ? { project_id: projectId } : {}),
              }),
            })
          : await fetch('/api/chat', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              signal: ac.signal,
              body: JSON.stringify({
                text: opts.text ?? '',
                retry: !!opts.retry,
                focus_entity_id: focusEntityId,
                thread_id: threadId,
                ...(projectId ? { project_id: projectId } : {}),
                ...(annot ? { annotation_image: annot.image, annotation_note: annot.note } : {}),
              }),
            })
        // Reattach mode: we already know the run_id; set it now so Stop
        // works before the first event arrives.
        if (opts.reattachRunId) currentRunIdRef.current = opts.reattachRunId
        if (!res.body) throw new Error('No response body')

        // W2-#4 phase 2 — the read loop is now lib/sseReader.ts. This
        // closure consumes each parsed event; returning 'terminal' tells
        // the helper to stop draining (the SSE-terminal branches —
        // cancelled / done / error — return 'terminal' AFTER setting
        // their state, mirroring the prior `return` semantics). The
        // (!live()) thread-switch bail also returns 'terminal' so the
        // helper cancels the reader cleanly.
        //
        // Helper's terminal reasons (see lib/sseReader.ts):
        //   'done'       — onEvent returned 'terminal'
        //   'cancelled'  — ac.signal aborted (Stop/Steer path)
        //   'premature'  — server closed without terminal SSE event
        //                  (the 2026-06-09 reload-mid-stream class)
        // We dispatch on that below.
        const terminal = await readSSEStream({
          // Hand the existing fetch in via a closure — caller already
          // built the right URL/method/body above. The helper passes its
          // own AbortSignal which we don't need to chain (ours is the
          // single source of cancel via `ac.signal`).
          fetcher: async () => res,
          onEvent: (raw): void | 'terminal' => {
            if (!live()) return 'terminal'  // thread switched — drop the rest
            // Helper already JSON.parse'd raw frames; widen back to SSEEvent
            // for the existing per-event switch.
            const ev = raw as SSEEvent

            // C-1: every event carries `seq` (assigned by the TurnSink).
            // Persist to localStorage with the run_id so a hard reload
            // can reattach with `?since=<seq>` for the SAME run; a
            // mismatching persisted entry is treated as 0 in the mount
            // probe above. We persist only when we know the run_id.
            const evSeq = (ev as { seq?: number }).seq
            const rid = currentRunIdRef.current
            if (typeof evSeq === 'number' && rid) {
              if (lastRunIdRef.current !== rid) {
                // Fresh turn — reset and start tracking under the new run_id.
                lastRunIdRef.current = rid
                lastSeqRef.current = 0
              }
              if (evSeq > lastSeqRef.current) {
                lastSeqRef.current = evSeq
                writePersistedSeq(threadId, rid, evSeq)
              }
            }

            // Observability Console: capture every event (except chat-text
            // deltas) into a bounded tail.
            const _le = logFor(ev)
            if (_le) setEventLog(prev => {
              const capped = prev.length > 400 ? prev.slice(-400) : prev
              // Coalesce a run of progress ticks into a single updating line —
              // a long download stays legible without flooding the capped buffer
              // or evicting the surrounding tool_start/result history.
              const last = capped[capped.length - 1]
              if (_le.type === 'tool_progress' && last && last.type === 'tool_progress') {
                return [...capped.slice(0, -1), _le]
              }
              return capped.concat(_le)
            })

            if (ev.type === 'job_submitted') {
              // Jobs tab: upsert by id.
              const j = ev.job
              setJobs(prev => {
                const rest = prev.filter(x => x.id !== j.id)
                return [...rest, { id: j.id, status: j.status || 'queued', title: j.title, t: Date.now() }]
              })
            }

            if (ev.type === 'notice') {
              // Transient status (e.g. "Model is busy — retrying…"). Shown
              // while we wait; cleared as soon as real content arrives.
              setStreamMsg({
                id: assistantId, role: 'assistant',
                blocks: [...streamingBlocks, { type: 'notice', text: ev.text }],
              })
            } else if (ev.type === 'delta') {
              const last = streamingBlocks[streamingBlocks.length - 1]
              if (last && last.type === 'text') {
                ;(last as { type: 'text'; text: string }).text += ev.text
              } else {
                streamingBlocks.push({ type: 'text', text: ev.text })
              }
              setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })
            } else if (ev.type === 'tool_start') {
              streamingBlocks.push({
                type: 'tool_start', name: ev.name, input: ev.input,
                tool_use_id: ev.tool_use_id,
              })
              setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })
            } else if (ev.type === 'tool_progress') {
              // Surface the live phase line on the running tool so a long
              // install/compile/download shows movement, not a dead spinner.
              // Match by tool_use_id when provided (post-#334), else last block.
              const target = ev.tool_use_id
                ? streamingBlocks.findLast(b => b.type === 'tool_start'
                    && (b as { tool_use_id?: string }).tool_use_id === ev.tool_use_id)
                : streamingBlocks[streamingBlocks.length - 1]
              if (target && target.type === 'tool_start') {
                ;(target as { type: 'tool_start'; progress?: string }).progress = ev.message
                setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })
              }
            } else if (ev.type === 'tool_chunk') {
              // #334 Phase 1 — live-tail of run_python / run_r stdout/stderr
              // routed to the originating tool_start block. Append per-stream;
              // track bytes/elapsed for the button-side live indicator.
              //
              // bytes_total dedupe (Phase 2): the same chunk can arrive twice
              // — once via the live SSE stream, once on a /tool_stream
              // rehydrate or an SSE replay-from-since. Each chunk carries the
              // CUMULATIVE byte counter; if ≤ what we've already applied for
              // this stream, the chunk is fully subsumed → skip. If only
              // PARTIALLY new (rehydrate landed mid-chunk), apply the tail.
              const target = streamingBlocks.findLast(b => b.type === 'tool_start'
                && (b as { tool_use_id?: string }).tool_use_id === ev.tool_use_id)
              if (target && target.type === 'tool_start') {
                const t = target as {
                  type: 'tool_start';
                  liveStdout?: string; liveStderr?: string;
                  liveBytesStdout?: number; liveBytesStderr?: number;
                  liveElapsedS?: number; lastChunkAt?: number;
                }
                const currentBytes = ev.stream === 'stderr'
                  ? (t.liveBytesStderr || 0) : (t.liveBytesStdout || 0)
                if (ev.bytes_total > currentBytes) {
                  const newBytes = ev.bytes_total - currentBytes
                  const tail = ev.text.length <= newBytes
                    ? ev.text : ev.text.slice(-newBytes)
                  if (ev.stream === 'stderr') {
                    t.liveStderr = (t.liveStderr || '') + tail
                    t.liveBytesStderr = ev.bytes_total
                  } else {
                    t.liveStdout = (t.liveStdout || '') + tail
                    t.liveBytesStdout = ev.bytes_total
                  }
                  t.liveElapsedS = ev.elapsed_s
                  t.lastChunkAt = Date.now()
                  setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })
                }
              }
            } else if (ev.type === 'tool_result') {
              streamingBlocks.push({ type: 'tool_result', name: ev.name, result: ev.result, tool_use_id: ev.tool_use_id })
              const plots = (ev.result as Record<string, unknown>).plots as
                | { url: string; original_name: string; preview_url?: string }[]
                | undefined
              const _exec_id_live = (ev.result as Record<string, unknown>).exec_id
              const _exec_id = (typeof _exec_id_live === 'string') ? _exec_id_live : undefined
              if (plots) {
                for (let i = 0; i < plots.length; i++) {
                  const p = plots[i]
                  streamingBlocks.push({
                    type: 'image', url: p.url, alt: p.original_name,
                    artifact_id: _exec_id ? `${_exec_id}:figure:${i}` : undefined,
                    preview_url: p.preview_url,
                  })
                }
              }
              setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })
            } else if (ev.type === 'plan') {
              // T2.5: forward all structured fields. Steps may be strings
              // (legacy / coerced) or PlanStepShape objects.
              streamingBlocks.push({
                type: 'plan',
                title: ev.title,
                summary: ev.summary,
                rationale: ev.rationale,
                assumptions: ev.assumptions,
                steps: ev.steps,
                concerns: ev.concerns,
              })
              setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })
            } else if (ev.type === 'deferred_tool_pending') {
              // Fix #5 — tool returned {deferred:true,job_id}. Clear the
              // spinner on its tool_start chip (turn is halted in
              // AWAITING_TOOL_RESULT; webhook posts the real tool_result
              // later when the job finishes). Without this the chip spins
              // for the whole job duration, defeating background-mode UX.
              const target = streamingBlocks.findLast(b => b.type === 'tool_start'
                && (b as { tool_use_id?: string }).tool_use_id === ev.tool_use_id)
              if (target && target.type === 'tool_start') {
                ;(target as { type: 'tool_start'; deferred?: boolean; deferredJobId?: string }).deferred = true
                ;(target as { type: 'tool_start'; deferredJobId?: string }).deferredJobId = ev.deferred_id
                setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })
              }
            } else if (ev.type === 'clarification_pending') {
              // B1 — Guide paused the turn on ask_clarification. Show the
              // question with an inline mini-composer; user's reply goes to
              // /api/turns/{run_id}/resume.
              streamingBlocks.push({ type: 'notice', text: `?  ${ev.question}` })
              setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })
              setPendingClarification({ runId: ev.run_id, question: ev.question })
            } else if (ev.type === 'approval_pending') {
              // P1 #3 — a flagged tool wants explicit approval before running.
              // Rare by design; the ApprovalBar surfaces the tool name + a
              // short summary of what it's about to do.
              streamingBlocks.push({ type: 'notice', text: `Approve ${ev.tool_name}? ${ev.summary}` })
              setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })
              setPendingApproval({
                runId: ev.run_id, toolName: ev.tool_name,
                summary: ev.summary, policy: ev.policy,
              })
            } else if (ev.type === 'manifest') {
              // T2.4: drawer sidecar. Also carries run_id so Stop can
              // target the right turn (manifest is the first SSE event).
              setManifest(ev.manifest)
              if (ev.run_id) currentRunIdRef.current = ev.run_id
            } else if (ev.type === 'cancelled') {
              // Backend confirmed the turn was cancelled. Render a
              // "(cancelled)" notice in chat so the user knows their
              // Stop click took effect (not just an aborted stream).
              streamingBlocks.push({ type: 'notice', text: '(cancelled)' })
              setMessages(prev => [...prev, { id: assistantId, role: 'assistant', blocks: [...streamingBlocks] }])
              setStreamMsg(null)
              setStreaming(false)
              currentRunIdRef.current = null
              // Steer path: this cancel was preceded by enqueue(text);
              // send the queued message now. Plain Stop path: drop the
              // queue. The distinction is the steerFlushRef flag set
              // by steer() before it fires cancel.
              if (steerFlushRef.current && queuedMessageRef.current) {
                const q = queuedMessageRef.current
                steerFlushRef.current = false
                // Don't clear the ref until the flush actually runs — if
                // setTimeout never fires (tab throttle, unmount), the
                // queue stays available for the next attempt instead of
                // disappearing silently. See the matching `done` branch.
                setTimeout(() => {
                  const flush = flushSendRef.current
                  if (!flush) {
                    console.warn('[useChat] steer auto-flush: flushSendRef null; queue preserved')
                    return
                  }
                  queuedMessageRef.current = null
                  setQueuedMessage(null)
                  flush(q)
                }, 0)
              } else {
                steerFlushRef.current = false
                queuedMessageRef.current = null
                setQueuedMessage(null)
              }
              return 'terminal'
            } else if (ev.type === 'entity_registered') {
              onERRef.current?.()
            } else if (ev.type === 'done') {
              setMessages(prev => [...prev, { id: assistantId, role: 'assistant', blocks: [...streamingBlocks] }])
              setStreamMsg(null)
              setStreaming(false)
              currentRunIdRef.current = null
              // Refresh entities so post-turn background updates surface — e.g.
              // a silently-refined thread question (guide-owned) shows in the brief.
              onERRef.current?.()
              // Auto-flush any queued message so the user can think+type
              // while the agent works. The clear happens INSIDE the
              // timeout — so if the callback never fires (tab throttle,
              // unmount, render race) the queue persists rather than
              // vanishing without sending. Live bug, 2026-06-11
              // (prj_128380fd thr_deed230d): user queued 'what model are
              // you?' mid-turn; the chip cleared on done but no message
              // ever landed in chat. The clear-before-schedule pattern
              // had no recovery path; this fix preserves the queue on
              // any failure path.
              if (queuedMessageRef.current) {
                const q = queuedMessageRef.current
                setTimeout(() => {
                  const flush = flushSendRef.current
                  if (!flush) {
                    console.warn('[useChat] auto-flush: flushSendRef null; queue preserved')
                    return
                  }
                  queuedMessageRef.current = null
                  setQueuedMessage(null)
                  flush(q)
                }, 0)
              }
              return 'terminal'
            } else if (ev.type === 'error') {
              streamingBlocks.push({ type: 'error', text: ev.text, detail: ev.detail })
              setMessages(prev => [...prev, { id: assistantId, role: 'assistant', blocks: [...streamingBlocks] }])
              setStreamMsg(null)
              setStreaming(false)
              currentRunIdRef.current = null
              // Don't auto-flush on error — user probably wants to see
              // the error and decide whether their queued message is
              // still appropriate. Keep the queue.
              return 'terminal'
            }
          },  // /onEvent
          abortSignal: ac.signal,
        })

        // Dispatch on the helper's terminal reason. 'done' = onEvent
        // already did all its work + state cleanup before returning
        // 'terminal'; nothing to do. 'cancelled' = ac aborted (Stop /
        // Steer / thread switch — the catch block below handles that
        // path too). 'premature' = the recovery branch.
        if (terminal === 'premature' && live()) {
          const rid = currentRunIdRef.current
          setStreamMsg(null)
          setStreaming(false)
          if (rid) {
            streamingBlocks.push({ type: 'notice', text: 'Connection dropped — reattaching…' })
            setMessages(prev => [...prev, { id: assistantId, role: 'assistant', blocks: [...streamingBlocks] }])
            const since = lastSeqRef.current
            setTimeout(() => {
              if (!live()) return
              runStreamRef.current?.({ reattachRunId: rid, since })
            }, 250)
          }
        }
      } catch (e) {
        // Aborted by a thread/project switch, or superseded — drop it silently
        // so nothing leaks into the new thread.
        if (ac.signal.aborted || !live()) return
        setStreamMsg(null)
        setStreaming(false)
        setMessages(prev => [
          ...prev,
          {
            id: `err-${Date.now()}`,
            role: 'assistant',
            blocks: [{ type: 'error', text: "Couldn't reach the server.", detail: String(e) }],
          },
        ])
      }
    },
    // projectId MUST be in deps — without it the closure captures whatever
    // projectId was at first mount, and POST /api/chat sends the STALE pid in
    // the request body → backend _require_project_context dutifully switches
    // to the wrong project → messages get saved in the wrong DB. PK 2026-06-03
    // observed: live thread in prj_4b07b6ef, but the "add a gene" turn landed
    // in prj_7b97dad2 because the closure was holding the prior pid. Same
    // bug shape as loadMessages had — fixed there in commit cb8658e.
    [focusEntityId, threadId, projectId],
  )
  // Expose runStream via a ref so loadMessages (declared above) can call
  // it without a temporal dead zone. Updated on every render — refs are
  // stable across renders even though runStream's identity changes.
  useEffect(() => { runStreamRef.current = runStream }, [runStream])

  const sendMessage = useCallback(
    async (text: string, annotation?: Annotation | null) => {
      if (streaming) return
      // Set streamingRef SYNCHRONOUSLY (the state-driven effect at line
      // 202 runs after React renders, leaving a window where an
      // in-flight loadMessages can resolve and `setMessages(server-
      // history)` wipes the optimistic user bubble before SSE delivers
      // the assistant turn. Race observed 2026-06-12 on a freshly-
      // opened project: project-switch kicks loadMessages, user types
      // and sends before that fetch returns, then the empty-history
      // response overwrites the just-added user message. PK
      streamingRef.current = true
      setMessages(prev => [...prev, {
        id: `u-${Date.now()}`, role: 'user', blocks: [{ type: 'text', text }],
      }])
      await runStream({ text, annotation })
    },
    [streaming, runStream],
  )
  // Keep the ref pointing at the latest sendMessage so the auto-flush
  // code inside the SSE handler (set up via closure on an older render)
  // can dispatch the queued message correctly.
  sendMessageRef.current = (text: string) => sendMessage(text)
  flushSendRef.current = (text: string) => {
    // Same sync-streamingRef flip as sendMessage — the auto-flush
    // can fire in the same tick as a project-switch'd loadMessages.
    streamingRef.current = true
    setMessages(prev => [...prev, { id: `u-${Date.now()}`, role: 'user', blocks: [{ type: 'text', text }] }])
    runStream({ text })
  }

  // Re-run the last turn after a failure. Completed steps (assistant turns +
  // tool results) were persisted server-side *during* the turn — only the error
  // block is frontend-only. So we RELOAD the saved conversation (restoring the
  // plan + finished steps, dropping the error) and let the backend continue from
  // where it left off, rather than discarding all the intermediate work.
  const retryLast = useCallback(async () => {
    if (streaming) return
    await loadMessages()
    await runStream({ retry: true })
  }, [streaming, runStream, loadMessages])

  // B1 — resume a paused turn with the user's clarification answer. Pushes
  // the answer into the visible message log first so it reads like a
  // normal back-and-forth.
  const answerClarification = useCallback(
    async (text: string) => {
      if (streaming || !pendingClarification) return
      streamingRef.current = true   // see sendMessage for the race-fix rationale
      setMessages(prev => [...prev, {
        id: `u-${Date.now()}`, role: 'user', blocks: [{ type: 'text', text }],
      }])
      await runStream({ text, resumeRunId: pendingClarification.runId })
    },
    [streaming, pendingClarification, runStream],
  )

  // Cancel the in-flight turn. Stop = pure cancel; queue is DROPPED
  // (user reasserts control). The 'cancelled' SSE handler sees
  // steerFlushRef=false and clears the queue without sending it.
  //
  // 2026-05-31: the backend's POST always returns 200 — `killed:false`
  // when the named token isn't live (e.g. our `currentRunIdRef` went
  // stale across turn boundaries). The backend falls back to the lone
  // active turn when there's exactly one; we also retry once after
  // 2.5s if streaming is still up — covers the race where the first
  // cancel hits between turns.
  const stopTurn = useCallback(async () => {
    const rid = currentRunIdRef.current
    if (!rid) return
    steerFlushRef.current = false   // make sure cancelled-handler treats this as Stop
    const fire = () => fetch(`/api/turns/${encodeURIComponent(rid)}/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    }).catch(() => null)
    await fire()
    setTimeout(() => {
      if (streamingRef.current && currentRunIdRef.current) fire()
    }, 2500)
  }, [])

  // Enqueue: type-while-streaming. Will auto-flush when the current
  // turn ends (done) OR when the user Steers (cancel+flush).
  const enqueue = useCallback((text: string) => {
    const t = text.trim()
    if (!t) { setQueuedMessage(null); queuedMessageRef.current = null; return }
    setQueuedMessage(t); queuedMessageRef.current = t
  }, [])

  const dropQueue = useCallback(() => { setQueuedMessage(null); queuedMessageRef.current = null }, [])

  // Steer: cancel the current turn AND send `text` once cancelled
  // commits. Sets the flush flag so the cancelled-handler knows this
  // wasn't a plain Stop. If text is empty, no-op (the user can hit
  // Stop alone if that's what they want).
  const steer = useCallback(async (text: string) => {
    const t = text.trim()
    if (!t) return
    const rid = currentRunIdRef.current
    if (!rid) {
      // Nothing in flight — just send directly.
      await sendMessage(t)
      return
    }
    steerFlushRef.current = true
    setQueuedMessage(t); queuedMessageRef.current = t
    try {
      await fetch(`/api/turns/${encodeURIComponent(rid)}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_text: 'steer' }),
      })
    } catch { /* if cancel fails, on-done flush still picks up the queue */ }
  }, [sendMessage])

  // P1 #3 — respond to a pending tool approval. The held tool runs (or
  // gets a rejection result) in the resume endpoint; the new turn then
  // streams normally with the result already in history.
  const respondApproval = useCallback(
    async (action: 'approve' | 'approve_session' | 'reject') => {
      if (streaming || !pendingApproval) return
      await runStream({ resumeRunId: pendingApproval.runId, approvalAction: action })
    },
    [streaming, pendingApproval, runStream],
  )

  return {
    messages, streaming, streamMsg, sendMessage, retryLast, loading, manifest,
    pendingClarification, answerClarification,
    pendingApproval, respondApproval,
    stopTurn,
    queuedMessage, enqueue, dropQueue, steer,
    eventLog, jobs,
    // #334 Phase 2 — passed to <Message> → <ToolStep> so an orphan tool_start
    // (cancelled run, completed-but-tab-refreshed) can rehydrate its live
    // output via GET /api/turns/{currentRunId}/tool_stream/{tool_use_id}.
    currentRunId: currentRunIdRef.current,
  }
}
