import { useState, useEffect, useRef, useCallback } from 'react'
import { DisplayMessage, Block, SSEEvent } from './types'

function rawToDisplay(raw: { role: string; content: unknown[]; ts?: string }, idx: number): DisplayMessage {
  const blocks: Block[] = []
  for (const block of raw.content as Record<string, unknown>[]) {
    if (block.type === 'text') {
      blocks.push({ type: 'text', text: block.text as string })
    } else if (block.type === 'tool_use') {
      // Show tool calls that are in history
      blocks.push({
        type: 'tool_start',
        name: block.name as string,
        input: (block.input ?? {}) as Record<string, unknown>
      })
    } else if (block.type === 'tool_result') {
      // Parse tool result content
      try {
        const content = block.content as string
        const parsed = JSON.parse(content)
        blocks.push({ type: 'tool_result', name: '(result)', result: parsed })
        // If result has plots, add image blocks
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
  return {
    id: `hist-${idx}`,
    role: raw.role as 'user' | 'assistant',
    blocks,
    ts: raw.ts,
  }
}

export function useChat() {
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [streamMsg, setStreamMsg] = useState<DisplayMessage | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Load history on mount
  useEffect(() => {
    fetch('/api/history')
      .then(r => r.json())
      .then((raw: { role: string; content: unknown[]; ts?: string }[]) => {
        // Collapse tool_use + tool_result pairs into assistant messages for display
        const display: DisplayMessage[] = []
        for (let i = 0; i < raw.length; i++) {
          const m = raw[i]
          // Skip pure tool_result messages (role=user but content is tool_results)
          if (m.role === 'user') {
            const allToolResults = (m.content as Record<string,unknown>[]).every(
              b => b.type === 'tool_result'
            )
            if (allToolResults) continue
          }
          display.push(rawToDisplay(m, i))
        }
        setMessages(display)
      })
      .catch(console.error)
  }, [])

  const sendMessage = useCallback(async (text: string) => {
    if (streaming) return
    const userMsg: DisplayMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      blocks: [{ type: 'text', text }],
    }
    setMessages(prev => [...prev, userMsg])
    setStreaming(true)

    // Initial streaming assistant message
    const assistantId = `a-${Date.now()}`
    const streamingBlocks: Block[] = []
    setStreamMsg({ id: assistantId, role: 'assistant', blocks: [] })

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
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
          try { ev = JSON.parse(raw) } catch { continue }

          if (ev.type === 'delta') {
            // Accumulate text into last text block or create new one
            const last = streamingBlocks[streamingBlocks.length - 1]
            if (last && last.type === 'text') {
              (last as { type: 'text'; text: string }).text += ev.text
            } else {
              streamingBlocks.push({ type: 'text', text: ev.text })
            }
            setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })

          } else if (ev.type === 'tool_start') {
            streamingBlocks.push({ type: 'tool_start', name: ev.name, input: ev.input })
            setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })

          } else if (ev.type === 'tool_result') {
            streamingBlocks.push({ type: 'tool_result', name: ev.name, result: ev.result })
            // Add image blocks for any plots
            const plots = (ev.result as Record<string,unknown>).plots as { url: string; original_name: string }[] | undefined
            if (plots) {
              for (const p of plots) {
                streamingBlocks.push({ type: 'image', url: p.url, alt: p.original_name })
              }
            }
            setStreamMsg({ id: assistantId, role: 'assistant', blocks: [...streamingBlocks] })

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
      setMessages(prev => [...prev, {
        id: `err-${Date.now()}`,
        role: 'assistant',
        blocks: [{ type: 'text', text: `Connection error: ${e}` }]
      }])
    }
  }, [streaming])

  return { messages, streaming, streamMsg, sendMessage }
}
