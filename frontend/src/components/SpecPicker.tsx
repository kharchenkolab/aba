/**
 * SpecPicker — per-thread primary-AgentSpec selector.
 *
 * Renders a compact dropdown of registered primary specs (typically
 * "guide" and "lean_guide"). The dropdown's choice is persisted on
 * the thread via PATCH /api/threads/{tid} with body {"spec": "<name>"}.
 *
 * "Default" maps to an empty string, which the backend interprets as
 * "clear the per-thread pin" — the thread falls through to the
 * ABA_PRIMARY_SPEC env var / "guide" default at the next turn.
 *
 * Lives in ThreadHeader, next to the lifecycle row.
 */
import { useEffect, useState } from 'react'
import './SpecPicker.css'


export interface SpecInfo {
  name: string
  model: string
  prompt_mode: 'full' | 'lean'
  tool_count: number | null
  summary_budget: number | null
  is_default: boolean
}


interface Props {
  /** Currently pinned spec on the thread, or null when nothing's
   *  pinned (thread inherits env/default). */
  pinned: string | null
  /** Caller updates the thread (typically a PATCH) and refreshes its
   *  view. Passing "" clears the pin. */
  onChange: (next: string) => Promise<void> | void
  /** Optional override for the catalog endpoint — primarily for tests
   *  that want to inject a fixture rather than mocking fetch. */
  specsEndpoint?: string
}


export default function SpecPicker({ pinned, onChange,
                                     specsEndpoint = '/api/specs/primary' }: Props) {
  const [specs, setSpecs] = useState<SpecInfo[]>([])
  const [defaultName, setDefaultName] = useState<string>('guide')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    fetch(specsEndpoint)
      .then(r => r.ok ? r.json() : { specs: [], default: 'guide' })
      .then(d => {
        if (cancelled) return
        setSpecs(d.specs || [])
        setDefaultName(d.default || 'guide')
        setLoading(false)
      })
      .catch(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [specsEndpoint])

  // Hide entirely when there's only one primary registered (no real
  // choice to make — keeps the header uncluttered for plain installs).
  if (!loading && specs.length < 2) return null

  // The dropdown's value: "" means "use default" (env or fallback);
  // any other value names a specific spec.
  const value = pinned ?? ''
  const handleChange = async (next: string) => { await onChange(next) }

  const labelFor = (s: SpecInfo) => {
    // Short-form: "guide (full, 60 tools)" / "lean_guide (lean, 16 tools)".
    const toolPart = s.tool_count === null ? 'all tools' : `${s.tool_count} tools`
    return `${s.name} — ${s.prompt_mode}, ${toolPart}`
  }

  return (
    <div className="spec-picker" role="group" aria-label="Backend">
      <span className="spec-picker__label">Backend</span>
      <select
        className="spec-picker__select"
        value={value}
        onChange={e => handleChange(e.target.value)}
        disabled={loading}
        title={value
          ? `This thread is pinned to ${value}.`
          : `Inheriting default (${defaultName}). Pick a spec to override per-thread.`}
      >
        <option value="">Default ({defaultName})</option>
        {specs.map(s => (
          <option key={s.name} value={s.name}>{labelFor(s)}</option>
        ))}
      </select>
    </div>
  )
}
