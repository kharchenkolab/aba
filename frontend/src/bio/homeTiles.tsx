/**
 * Home-tile registry — bio side of the platform/bio split.
 *
 * Home.tsx shows a few "stats tiles" per project (threads, claims,
 * datasets, runs, results). The labels and the count-from-entity-counts
 * rule are biology-flavored — runs aggregate the `analysis` entity type
 * (so the platform calls it "runs"), results aggregate every artifact
 * type that can ride inside a Result, etc.
 *
 * Each tile registers a `count(counts)` derivation. The shell collects
 * `home_tiles()` and renders one stat per tile. Adding a new tile is a
 * single `register_home_tile()` call.
 */

export interface HomeTile {
  /** Stable key used as React key + in the URL/HTML. */
  key: string
  /** Plural display label ("threads"). */
  label: string
  /** Derive the stat count from a project-counts map (raw entity-type tallies). */
  count(counts: Record<string, number>): number
}

const _TILES: HomeTile[] = []

/** Register a home tile. Order = registration order. */
export function register_home_tile(tile: HomeTile): void {
  _TILES.push(tile)
}

/** All registered tiles in registration order. */
export function home_tiles(): HomeTile[] {
  return [..._TILES]
}

/** A stable per-key lookup for callers that don't want to render the
 *  whole list (e.g. project-card row that surfaces only a subset). */
export function home_tile_for(key: string): HomeTile | null {
  return _TILES.find(t => t.key === key) ?? null
}


// ---------- Default bio home tiles ----------
// Mirror the prior HOME_STATS constant in Home.tsx — the projectCount()
// special-case for runs/results is folded into each tile's count() now.

register_home_tile({
  key: 'thread',
  label: 'threads',
  count: c => c.thread ?? 0,
})
register_home_tile({
  key: 'claim',
  label: 'claims',
  count: c => c.claim ?? 0,
})
register_home_tile({
  key: 'dataset',
  label: 'datasets',
  count: c => c.dataset ?? 0,
})
register_home_tile({
  key: 'runs',
  label: 'runs',
  // `runs` rolls up analysis (the new canonical type) and the legacy
  // `run` alias — same special-case the old projectCount() carried.
  count: c => c.analysis ?? c.run ?? 0,
})
register_home_tile({
  key: 'results',
  label: 'results',
  // Sum the artifact types that ride inside a Result, so the home count
  // matches the "Results tab" definition: figures + tables + result +
  // note + narrative.
  count: c => ['figure', 'table', 'result', 'note', 'narrative']
                .reduce((sum, t) => sum + (c[t] ?? 0), 0),
})
