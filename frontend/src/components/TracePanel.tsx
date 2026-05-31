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

function summInput(name: string, input: Record<string, unknown>): string {
  const s = (v: unknown, n = 60) => { const x = String(v ?? ''); return x.length > n ? x.slice(0, n) + '…' : x }
  if (name === 'run_python' || name === 'run_r') {
    const code = String(input?.code ?? '')
    return code ? `${code.split('\n')[0].slice(0, 70)}  · ${code.length}ch` : ''
  }
  if (name === 'read_skill')       return s(input?.name)
  if (name === 'search_skills')    return `"${s(input?.query, 50)}"`
  if (name === 'ensure_capability')return s(input?.name)
  if (name === 'register_dataset') return s(input?.title)
  if (name === 'present_plan')     return s(input?.title)
  if (name === 'fetch_url')        return s(input?.url, 70)
  if (name === 'list_data_files')  return ''
  const keys = Object.keys(input || {}).slice(0, 3)
  return keys.map(k => `${k}=${s(input[k], 30)}`).join(' ')
}

function statusLine(step: TraceStep): string {
  const verb: Record<string, string> = {
    list_data_files: 'list data files', read_csv_info: 'read CSV',
    run_python: 'run Python', run_r: 'run R', read_skill: 'read recipe',
    search_skills: 'search recipes', ensure_capability: 'install',
    register_dataset: 'register dataset', present_plan: 'present plan',
    open_run: 'open run', close_run: 'close run',
    promote_to_result: 'promote to result', pin_entity: 'pin', create_finding: 'create finding',
    create_claim: 'create claim', fetch_url: 'fetch URL',
  }
  const action = verb[step.name] ?? step.name
  const detail = summInput(step.name, step.input)
  const title = detail ? `${action}: ${detail}` : action
  if (!step.result) return `${title[0].toUpperCase()}${title.slice(1)}…`
  if ('error' in step.result || step.result?.status === 'blocked') return title
  return title[0].toUpperCase() + title.slice(1)
}

interface Badge { label: string; cls: string; title?: string }

function badges(result: Record<string, unknown> | null): Badge[] {
  if (!result) return []
  const out: Badge[] = []
  if (result.status === 'blocked')
    out.push({ label: `BLOCKED · ${result.block_type ?? 'veto'}`, cls: 'badge--blocked',
               title: String(result.message ?? '') })
  if (result.recipe_hint)
    out.push({ label: 'recipe hint', cls: 'badge--info', title: String(result.recipe_hint) })
  if (result.fetch_warning)
    out.push({ label: 'fetch warning', cls: 'badge--warn', title: String(result.fetch_warning) })
  const w = result.guardrail_warnings
  if (Array.isArray(w) && w.length)
    out.push({ label: `guardrail × ${w.length}`, cls: 'badge--warn',
               title: w.map(String).join('\n\n') })
  if (result.status === 'error' || result.is_error)
    out.push({ label: 'error', cls: 'badge--err' })
  if (typeof result.returncode === 'number' && result.returncode !== 0)
    out.push({ label: `rc=${result.returncode}`, cls: 'badge--err' })
  return out
}

function PlanContent({ result, input }: { result: Record<string, unknown>; input: Record<string, unknown> }) {
  // present_plan: steps live on the INPUT (the agent's plan), result is just an ack.
  const steps = (input?.steps ?? input?.plan ?? result?.steps) as unknown
  if (!Array.isArray(steps) || !steps.length) return null
  return (
    <div className="trace-card__section">
      <div className="trace-card__section-head">plan steps</div>
      <ol className="trace-card__plan">
        {steps.map((s, i) => {
          const text = typeof s === 'string' ? s
            : (s as { title?: string; text?: string; description?: string })?.title
            ?? (s as { text?: string })?.text
            ?? (s as { description?: string })?.description
            ?? JSON.stringify(s)
          return <li key={i}>{text}</li>
        })}
      </ol>
    </div>
  )
}

// Catch-all: result fields not already rendered above — so nothing is invisible.
// Lets the developer surface returncode/status/run_id/entity_id/body/etc. that the
// trace previously dropped on the floor.
const _SHOWN_KEYS = new Set([
  'stdout', 'stderr', 'plots', 'error', 'returncode',
  'recipe_hint', 'fetch_warning', 'guardrail_warnings', 'is_error',
  'status', 'block_type', 'reason_code', 'message',
  'allowed_next_actions', 'forbidden_next_actions', 'note', 'executed',
  'files', 'steps', 'plan', 'body',  // body shown via its own excerpt
])

function OtherFields({ result }: { result: Record<string, unknown> }) {
  const extra = Object.entries(result).filter(([k, v]) =>
    !_SHOWN_KEYS.has(k) && v !== null && v !== undefined && v !== '')
  if (!extra.length) return null
  return (
    <details className="trace-card__section">
      <summary className="trace-card__section-head">other fields ({extra.length})</summary>
      <pre className="trace-card__observation">{JSON.stringify(Object.fromEntries(extra), null, 2)}</pre>
    </details>
  )
}

function TraceCard({ step, defaultExpanded }: { step: TraceStep; defaultExpanded: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const { icon, cls } = statusIcon(step)
  const r = step.result

  const code = typeof step.input?.code === 'string' ? (step.input.code as string) : null
  const stdout = typeof r?.stdout === 'string' ? (r.stdout as string).trim() : ''
  const stderr = typeof r?.stderr === 'string' ? (r.stderr as string).trim() : ''
  const errMsg = (r as { error?: string } | null)?.error
  const plots = (r as { plots?: { url: string; original_name?: string }[] } | null)?.plots ?? []
  const dur = formatDuration(step)
  const bs = badges(r)
  const blockedMsg = r?.status === 'blocked' ? String(r.message ?? '') : ''
  const skillBody = step.name === 'read_skill' && typeof r?.body === 'string' ? (r.body as string) : ''
  const blockedCls = r?.status === 'blocked' ? ' trace-card--blocked' : ''

  return (
    <div className={`trace-card ${cls}${blockedCls}`}>
      <button
        className="trace-card__header"
        onClick={() => setExpanded(v => !v)}
        type="button"
      >
        <span className="trace-card__icon">{icon}</span>
        <span className="trace-card__title">{statusLine(step)}</span>
        {bs.map((b, i) => (
          <span key={i} className={`trace-badge ${b.cls}`} title={b.title}>{b.label}</span>
        ))}
        {dur && <span className="trace-card__duration">{dur}</span>}
        <span className="trace-card__chev">{expanded ? '▾' : '▸'}</span>
      </button>

      {expanded && (
        <div className="trace-card__body">
          {blockedMsg && (
            <div className="trace-card__section">
              <div className="trace-card__section-head">veto / blocked</div>
              <pre className="trace-card__observation trace-card__observation--err">{blockedMsg}</pre>
              {Array.isArray(r?.allowed_next_actions) && (
                <pre className="trace-card__observation">
                  allowed next:
                  {'\n  - ' + (r!.allowed_next_actions as unknown[]).map(String).join('\n  - ')}
                </pre>
              )}
            </div>
          )}
          {step.name === 'present_plan' && r && <PlanContent result={r} input={step.input} />}
          {code && (
            <details className="trace-card__section" open>
              <summary className="trace-card__section-head">code ({code.length} ch)</summary>
              <pre className="trace-card__code"><code>{code}</code></pre>
            </details>
          )}
          {skillBody && (
            <details className="trace-card__section">
              <summary className="trace-card__section-head">recipe body ({skillBody.length} ch)</summary>
              <pre className="trace-card__observation">{skillBody}</pre>
            </details>
          )}
          {step.name === 'list_data_files' && r && Array.isArray(r.files) && (
            <pre className="trace-card__observation">
              {(r.files as { filename: string; size_bytes: number }[])
                .map(f => `${f.filename}  (${f.size_bytes} B)`).join('\n')}
            </pre>
          )}
          {Array.isArray(r?.guardrail_warnings) && r!.guardrail_warnings.length > 0 && (
            <div className="trace-card__section">
              <div className="trace-card__section-head">guardrail warnings</div>
              {(r!.guardrail_warnings as string[]).map((w, i) => (
                <pre key={i} className="trace-card__observation trace-card__observation--warn">{w}</pre>
              ))}
            </div>
          )}
          {typeof r?.recipe_hint === 'string' && (
            <div className="trace-card__section">
              <div className="trace-card__section-head">recipe hint</div>
              <pre className="trace-card__observation">{r.recipe_hint as string}</pre>
            </div>
          )}
          {typeof r?.fetch_warning === 'string' && (
            <div className="trace-card__section">
              <div className="trace-card__section-head">fetch warning</div>
              <pre className="trace-card__observation trace-card__observation--warn">{r.fetch_warning as string}</pre>
            </div>
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
                <img key={i} className="trace-card__plot" src={p.url} alt={p.original_name ?? 'plot'} />
              ))}
            </div>
          )}
          {r && <OtherFields result={r} />}
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
