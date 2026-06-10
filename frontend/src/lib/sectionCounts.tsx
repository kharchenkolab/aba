/**
 * Section-count registry — bio side of the platform/bio split.
 *
 * The shell's left rail surfaces a small set of "project sections"
 * (Threads, Data, Claims, Runs, Results, Files). The COUNT shown next
 * to each tab is derived from the active entities + bio rules ("a Run
 * is an `analysis` entity that isn't the ambient catch-all"; "Files
 * count = artifacts with an artifact_path"). Those rules are bio, so
 * the shell asks `section_count(name, entities)` and the bio registry
 * holds the rule.
 *
 * Today the section LIST is the legacy hardcoded shape (threads, data,
 * claims, runs, results, files) — moving the LIST is C3 territory.
 * For C2 we just need the counts to dispatch through bio so the shell
 * stops carrying the type literals.
 */
import type { Entity } from '../types'
import { type_in_class } from './entityClasses'

export type SectionName = 'threads' | 'claims' | 'data' | 'runs' | 'results' | 'files'

type CountFn = (entities: Entity[]) => number

const _COUNTS = new Map<string, CountFn>()

/** Register (or override) a section's count rule. */
export function register_section_count(name: string, fn: CountFn): void {
  _COUNTS.set(name, fn)
}

/** Compute the count for a section. Returns 0 when nothing is
 *  registered — caller can rely on the section's existence (it's a
 *  static enum elsewhere) and just see a 0 badge. */
export function section_count(name: string, entities: Entity[]): number {
  return _COUNTS.get(name)?.(entities) ?? 0
}

/** Compute all registered section counts in one shot. Convenience for
 *  the shell, which holds the full {threads, claims, ...} object. */
export function section_counts(entities: Entity[]): Record<SectionName, number> {
  return {
    threads: section_count('threads', entities),
    claims:  section_count('claims',  entities),
    data:    section_count('data',    entities),
    runs:    section_count('runs',    entities),
    results: section_count('results', entities),
    files:   section_count('files',   entities),
  }
}


// ---------- Default bio section-count rules ----------
// `entities` is already filtered down to active (non-archived,
// non-superseded) rows by the shell; rules below don't re-filter.

register_section_count('threads', es =>
  // +1 for the implicit Main thread that's always present.
  1 + es.filter(e => e.type === 'thread' && !e.metadata?.is_default).length,
)
register_section_count('claims',  es => es.filter(e => e.type === 'claim').length)
register_section_count('data',    es => es.filter(e => e.type === 'dataset').length)
register_section_count('runs',    es =>
  // Exclude the ambient catch-all analysis (lifecycle/registry.py:_ensure_analysis)
  // — structural bookkeeping, never user-facing.
  es.filter(e => e.type === 'analysis'
           && !(e.metadata as { ambient?: boolean } | undefined)?.ambient).length,
)
register_section_count('results', es => es.filter(e => e.type === 'result').length)
register_section_count('files',   es =>
  // Virtual files view shows artifacts that have an actual file (project
  // tree projection). Matches the legacy hardcoded artifact list.
  es.filter(e => type_in_class(e.type, 'artifact') && e.artifact_path).length,
)
