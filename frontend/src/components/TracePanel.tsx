/**
 * Trace stream — the agent's inner loop, separated from the main answer.
 *
 * Walks the conversation in order and renders each tool call as a card with:
 *   - status header (running / done)
 *   - producing code (collapsible)
 *   - observation output (collapsible, collapsed by default)
 *   - any figures produced (always visible inline)
 *
 * The "main" stream (user messages + Guide's natural-language answers) lives
 * in <ChatPane>'s left column. This panel is the right column when the user
 * has toggled the trace on.
 *
 * Pattern reference: biomni/agent/a1.py launch_gradio_demo() generate_response()
 * (per misc/aba_arch2.md §5.1) — code panel + observation, status pending→done.
 */
import { useState } from 'react'
import type { DisplayMessage } from '../types'
import './TracePanel.css'

interface TraceStep {
  id: string
  name: string
  input: Record<string, unknown>
  result: Record<string, unknown> | null // null = still running
}

/** Walk messages in order; each tool_start opens a new step; the next
 *  tool_result closes it. */
function extractSteps(messages: DisplayMessage[]): TraceStep[] {
  const steps: TraceStep[] = []
  let nextId = 0
  for (const m of messages) {
    for (const b of m.blocks) {
      if (b.type === 'tool_start') {
        steps.push({
          id: `step-${nextId++}`,
          name: b.name,
          input: b.input ?? {},
          result: null,
        })
      } else if (b.type === 'tool_result') {
        const open = steps.findLast(s => s.result === null && s.name === b.name)
          ?? steps[steps.length - 1]
        if (open && open.result === null) open.result = b.result
      }
    }
  }
  return steps
}

function formatDuration(_step: TraceStep): string {
  // Placeholder — backend currently doesn't emit duration. Phase 4 will.
  return ''
}

function statusIcon(step: TraceStep): { icon: string; cls: string } {
  if (!step.result) return { icon: '◐', cls: 'trace-card--running' }
  if ('error' in step.result) return { icon: '✗', cls: 'trace-card--error' }
  return { icon: '✓', cls: 'trace-card--done' }
}

function statusLine(step: TraceStep): string {
  const verb: Record<string, string> = {
    list_data_files: 'list data files',
    read_csv_info: 'read CSV',
    run_python: 'run Python',
  }
  const action = verb[step.name] ?? step.name
  if (!step.result) return `${action[0].toUpperCase()}${action.slice(1)}…`
  if ('error' in step.result) return `${action} — error`
  return action[0].toUpperCase() + action.slice(1)
}

function TraceCard({ step, defaultExpanded }: { step: TraceStep; defaultExpanded: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const { icon, cls } = statusIcon(step)

  const code = typeof step.input?.code === 'string' ? (step.input.code as string) : null
  const stdout =
    typeof step.result?.stdout === 'string' ? (step.result.stdout as string).trim() : ''
  const stderr =
    typeof step.result?.stderr === 'string' ? (step.result.stderr as string).trim() : ''
  const errMsg = (step.result as { error?: string } | null)?.error
  const plots = (step.result as { plots?: { url: string; original_name?: string }[] } | null)
    ?.plots ?? []
  const dur = formatDuration(step)

  return (
    <div className={`trace-card ${cls}`}>
      <button
        className="trace-card__header"
        onClick={() => setExpanded(v => !v)}
        type="button"
      >
        <span className="trace-card__icon">{icon}</span>
        <span className="trace-card__title">{statusLine(step)}</span>
        {dur && <span className="trace-card__duration">{dur}</span>}
        <span className="trace-card__chev">{expanded ? '▾' : '▸'}</span>
      </button>

      {expanded && (
        <div className="trace-card__body">
          {code && (
            <pre className="trace-card__code"><code>{code}</code></pre>
          )}
          {step.name === 'list_data_files' && step.result && Array.isArray(step.result.files) && (
            <pre className="trace-card__observation">
              {(step.result.files as { filename: string; size_bytes: number }[])
                .map(f => `${f.filename}  (${f.size_bytes} B)`)
                .join('\n')}
            </pre>
          )}
          {stdout && (
            <div className="trace-card__section">
              <div className="trace-card__section-head">stdout</div>
              <pre className="trace-card__observation">{stdout}</pre>
            </div>
          )}
          {stderr && (
            <div className="trace-card__section">
              <div className="trace-card__section-head">stderr</div>
              <pre className="trace-card__observation trace-card__observation--err">{stderr}</pre>
            </div>
          )}
          {errMsg && (
            <div className="trace-card__section">
              <div className="trace-card__section-head">error</div>
              <pre className="trace-card__observation trace-card__observation--err">{errMsg}</pre>
            </div>
          )}
          {plots.length > 0 && (
            <div className="trace-card__plots">
              {plots.map((p, i) => (
                <img
                  key={i}
                  className="trace-card__plot"
                  src={p.url}
                  alt={p.original_name ?? 'plot'}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

interface Props {
  messages: DisplayMessage[]
  streamMsg: DisplayMessage | null
}

export default function TracePanel({ messages, streamMsg }: Props) {
  const all = streamMsg ? [...messages, streamMsg] : messages
  const steps = extractSteps(all)

  if (steps.length === 0) {
    return (
      <aside className="trace">
        <div className="trace__head">Trace</div>
        <div className="trace__empty">
          The agent's inner loop will appear here — every tool call as a
          collapsible card.
        </div>
      </aside>
    )
  }

  return (
    <aside className="trace">
      <div className="trace__head">
        Trace
        <span className="trace__count">{steps.length}</span>
      </div>
      <div className="trace__list">
        {steps.map((s, i) => (
          <TraceCard
            key={s.id}
            step={s}
            defaultExpanded={i === steps.length - 1 && !s.result}
          />
        ))}
      </div>
    </aside>
  )
}

declare global {
  interface Array<T> {
    // Polyfill type — Node 20+ / modern browsers have this natively.
    findLast(predicate: (value: T) => boolean): T | undefined
  }
}
