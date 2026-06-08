/**
 * useResetOnChange — fire a reset callback whenever a watched value
 * changes. Does NOT fire on mount (initial value isn't a "change").
 *
 * Used by App to clear the in-flight annotation (image + framing note
 * attached by the SplitButton's "Chat about this figure" gesture) when
 * the focused entity changes. The annotation was attached for the
 * figure the user just clicked on; if they navigate to a different
 * entity before sending, the stale annotation would still ride along
 * on the next send and misframe the agent's reading. Pairs with the
 * server-side ephemeral-note fix in guide.py (focus regression
 * found 2026-06-07 in thr_806a2ced).
 */
import { useEffect, useRef } from 'react'

export function useResetOnChange<T>(value: T, onChange: () => void): void {
  const prev = useRef<T>(value)
  useEffect(() => {
    if (!Object.is(prev.current, value)) {
      onChange()
      prev.current = value
    }
  }, [value, onChange])
}
