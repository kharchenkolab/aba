import { useCallback, useEffect, useRef, useState } from 'react'
import type { DisplayMessage, Block, SSEEvent } from './types'

type RawMsg = { role: string; content: unknown[]; ts?: string }

function blocksFromContent(content: Record<string, unknown>[]): Block[] {
  const blocks: Block[] = []
  for (const block of content) {
    if (block.type === 'text') {
      blocks.push({ type: 'text', text: block.text as string })
    } else if (block.type === 'tool_use') {
      blocks.push({
        type: 'tool_start',
        name: block.name as string,
        input: (block.input ?? {}) as Record<string, unknown>,
      })
    } else if (block.type === 'tool_result') {
      try {
        const parsed = JSON.parse(block.content as string)
        blocks.push({ type: 'tool_result', name: '(result)', result: parsed })
        if (parsed.plots && Array.isArray(parsed.plots)) {
          for (const p of parsed.plots) {
            blocks.push({ type: 'image', url: p.url, alt: p.original_name })
          }
        }
      } catch {
        blocks.push({ type: 'text', text: String(block.content) })
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

export function useChat(
  focusEntityId: string,
  onEntityRegistered?: () => void,
) {
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [streamMsg, setStreamMsg] = useState<DisplayMessage | null>(null)
  const onERRef = useRef(onEntityRegistered)
  onERRef.current = onEntityRegistered

  // Load the project's conversation once on mount. The chat thread is
  // workspace-level — focus changes do NOT swap the conversation.
  useEffect(() => {
    let cancelled = false
    fetch('/api/messages')
      .then(r => (r.ok ? r.json() : Promise.reject(r)))
      .then((raw: RawMsg[]) => {
        if (cancelled) return
        setMessages(collapseHistory(raw))
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  const sendMessage = useCallback(
    async (text: string) => {
      if (streaming) return
      const userMsg: DisplayMessage = {
        id: `u-${Date.now()}`,
        role: 'user',
        blocks: [{ type: 'text', text }],
      }
      setMessages(prev => [...prev, userMsg])
      setStreaming(true)

      const assistantId = `a-${Date.now()}`
      const streamingBlocks: Block[] = []
      setStreamMsg({ id: assistantId, role: 'assistant', blocks: [] })

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, focus_entity_id: focusEntityId }),
        })
        if (!res.body) throw new Error('No response body')
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buf = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
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

            if (ev.type === 'delta') {
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
            } else if (ev.type === 'entity_registered') {
              onERRef.current?.()
            } else if (ev.type === 'done') {
              setMessages(prev => [...prev, { id: assistantId, role: 'assistant', blocks: [...streamingBlocks] }])
              setStreamMsg(null)
              setStreaming(false)
              return
            } else if (ev.type === 'error') {
              streamingBlocks.push({ type: 'text', text: `⚠️ Error: ${ev.text}` })
              setMessages(prev => [...prev, { id: assistantId, role: 'assistant', blocks: [...streamingBlocks] }])
              setStreamMsg(null)
              setStreaming(false)
              return
            }
          }
        }
      } catch (e) {
        setStreamMsg(null)
        setStreaming(false)
        setMessages(prev => [
          ...prev,
          {
            id: `err-${Date.now()}`,
            role: 'assistant',
            blocks: [{ type: 'text', text: `Connection error: ${e}` }],
          },
        ])
      }
    },
    [streaming, focusEntityId],
  )

  return { messages, streaming, streamMsg, sendMessage }
}
