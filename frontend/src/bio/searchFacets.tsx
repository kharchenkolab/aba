/**
 * Search-facet registry — bio side of the platform/bio split.
 *
 * The platform's SearchModal needs a placeholder hint that lists what
 * kinds of things the user can search ("figures, findings, datasets,
 * chat…"). That list is biology-flavored, so the facet labels live
 * here and the shell just calls `search_placeholder()` to build the
 * complete placeholder string.
 *
 * Today the registry holds DISPLAY-only labels — backend search already
 * walks every entity type by default. If a future facet needs to
 * restrict the query (e.g. only narratives), we'd extend the registered
 * value to a {label, queryParam} pair.
 */

const _FACETS: string[] = []

/** Register a facet label (display only). Order = registration order. */
export function register_search_facet(label: string): void {
  if (!_FACETS.includes(label)) _FACETS.push(label)
}

/** All registered facet labels, in registration order. */
export function search_facets(): string[] {
  return [..._FACETS]
}

/** Build the placeholder string for the search modal: "Search figures,
 *  findings, datasets, chat…". Falls back to a generic prompt when no
 *  facets are registered (the empty-bio case). */
export function search_placeholder(): string {
  if (_FACETS.length === 0) return 'Search…'
  return `Search ${_FACETS.join(', ')}, chat…`
}


// ---------- Default bio facets ----------
// Mirror the prior hardcoded SearchModal copy. "chat" is appended by
// `search_placeholder` itself — it's the only platform-level (not
// entity-typed) search target.

register_search_facet('figures')
register_search_facet('findings')
register_search_facet('datasets')
