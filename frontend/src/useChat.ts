import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { DisplayMessage, Block, SSEEvent, ManifestSnapshot, PendingClarification } from './types'

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
      // Orphan-fill tool_results (from a crashed prior turn) shouldn't
      // appear in the visible chat. Two historical formats:
      //   - new: JSON {status:'interrupted', note:...}
      //   - legacy: plain string starting with the marker
      const raw = block.content
      if (typeof raw === 'string' && raw.startsWith('[tool result unavailable')) {
        continue
      }
      try {
        const parsed = JSON.parse(raw as string)
        if (parsed && parsed.status === 'interrupted') continue   // orphan-fill (new format)
        if (parsed && parsed.status === 'presented') continue     // present_plan ack — the plan card already shows it
        if (parsed && parsed.status === 'asked') continue         // ask_clarification ack — the mini-composer already shows it
        blocks.push({ type: 'tool_result', name: '(result)', result: parsed })
        if (parsed.plots && Array.isArray(parsed.plots)) {
          for (const p of parsed.plots) {
            blocks.push({ type: 'image', url: p.url, alt: p.original_name })
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

export function useChat(
  focusEntityId: string,
  onEntityRegistered?: () => void,
  annotation?: Annotation | null,
  reloadKey?: unknown,
  threadId: string = 'default',
) {
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [loading, setLoading] = useState(false)   // fetching a thread's history
  const [streamMsg, setStreamMsg] = useState<DisplayMessage | null>(null)
  const [manifest, setManifest] = useState<ManifestSnapshot | null>(null)
  // B1 — when the Guide pauses on ask_clarification, the UI shows an
  // inline mini-composer. Cleared when the resume turn starts streaming.
  const [pendingClarification, setPendingClarification] = useState<PendingClarification | null>(null)
  const onERRef = useRef(onEntityRegistered)
  onERRef.current = onEntityRegistered
  const annotationRef = useRef(annotation)
  annotationRef.current = annotation
  // Each thread/project switch bumps the generation; any in-flight stream or
  // message load tagged with an older generation bails so it can't leak the old
  // thread's content into the new one.
  const genRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)

  // Load the current thread's persisted conversation (ignored if superseded).
  const loadMessages = useCallback(async () => {
    const myGen = genRef.current
    try {
      const r = await fetch(`/api/messages?thread_id=${encodeURIComponent(threadId)}`)
      const raw = (await r.json()) as RawMsg[]
      if (r.ok && genRef.current === myGen) setMessages(collapseHistory(raw))
    } catch { /* ignore */ }
    finally { if (genRef.current === myGen) setLoading(false) }
  }, [threadId])

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
    async (opts: { text?: string; retry?: boolean; annotation?: Annotation | null; resumeRunId?: string }) => {
      const myGen = genRef.current
      const ac = new AbortController()
      abortRef.current = ac
      setStreaming(true)
      // A resume implicitly accepts whatever pending question/plan we were on.
      if (opts.resumeRunId) setPendingClarification(null)
      const assistantId = `a-${Date.now()}`
      const streamingBlocks: Block[] = []
      setStreamMsg({ id: assistantId, role: 'assistant', blocks: [] })
      const live = () => genRef.current === myGen   // false once the thread switched

      // Sticky: the marked region stays attached across follow-up messages
      // so the agent retains it; the user clears it explicitly via the chip.
      // An explicit per-call annotation (e.g. "chat about this plot") wins.
      const annot = opts.annotation !== undefined ? opts.annotation : annotationRef.current

      try {
        const res = opts.resumeRunId
          ? await fetch(`/api/turns/${encodeURIComponent(opts.resumeRunId)}/resume`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              signal: ac.signal,
              body: JSON.stringify({ user_text: opts.text ?? '' }),
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
                ...(annot ? { annotation_image: annot.image, annotation_note: annot.note } : {}),
              }),
            })
        if (!res.body) throw new Error('No response body')
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buf = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          if (!live()) { await reader.cancel().catch(() => {}); return }   // thread switched — drop the rest
          buf += decoder.decode(value, { stream: true })
          const lines = buf.split('\n')
          buf = lines.pop() ?? ''

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            const raw = line.slice(6).trim()
            if (!raw) continue
            let ev: SSEEvent
            try {
              ev = JSON.parse(raw)
            } catch {
              continue
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
              streamingBlocks.push({ type: 'tool_start', name: ev.name, input: ev.input })
              setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })
            } else if (ev.type === 'tool_result') {
              streamingBlocks.push({ type: 'tool_result', name: ev.name, result: ev.result })
              const plots = (ev.result as Record<string, unknown>).plots as
                | { url: string; original_name: string }[]
                | undefined
              if (plots) {
                for (const p of plots) {
                  streamingBlocks.push({ type: 'image', url: p.url, alt: p.original_name })
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
            } else if (ev.type === 'clarification_pending') {
              // B1 — Guide paused the turn on ask_clarification. Show the
              // question with an inline mini-composer; user's reply goes to
              // /api/turns/{run_id}/resume.
              streamingBlocks.push({ type: 'notice', text: `?  ${ev.question}` })
              setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })
              setPendingClarification({ runId: ev.run_id, question: ev.question })
            } else if (ev.type === 'manifest') {
              // T2.4: drawer sidecar. The model only ever sees the rendered
              // system string; the JSON here is for visibility/inspection.
              setManifest(ev.manifest)
            } else if (ev.type === 'entity_registered') {
              onERRef.current?.()
            } else if (ev.type === 'done') {
              setMessages(prev => [...prev, { id: assistantId, role: 'assistant', blocks: [...streamingBlocks] }])
              setStreamMsg(null)
              setStreaming(false)
              // Refresh entities so post-turn background updates surface — e.g.
              // a silently-refined thread question (guide-owned) shows in the brief.
              onERRef.current?.()
              return
            } else if (ev.type === 'error') {
              streamingBlocks.push({ type: 'error', text: ev.text, detail: ev.detail })
              setMessages(prev => [...prev, { id: assistantId, role: 'assistant', blocks: [...streamingBlocks] }])
              setStreamMsg(null)
              setStreaming(false)
              return
            }
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
    [focusEntityId, threadId],
  )

  const sendMessage = useCallback(
    async (text: string, annotation?: Annotation | null) => {
      if (streaming) return
      setMessages(prev => [...prev, {
        id: `u-${Date.now()}`, role: 'user', blocks: [{ type: 'text', text }],
      }])
      await runStream({ text, annotation })
    },
    [streaming, runStream],
  )

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
      setMessages(prev => [...prev, {
        id: `u-${Date.now()}`, role: 'user', blocks: [{ type: 'text', text }],
      }])
      await runStream({ text, resumeRunId: pendingClarification.runId })
    },
    [streaming, pendingClarification, runStream],
  )

  return {
    messages, streaming, streamMsg, sendMessage, retryLast, loading, manifest,
    pendingClarification, answerClarification,
  }
}
