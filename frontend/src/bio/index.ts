/**
 * Bio aggregator — single entry point the platform shell imports to
 * trigger all bio-side registrations (focus views, rail icons, menu
 * traits, search facets, home tiles).
 *
 * App.tsx does `import './bio'` once on startup; each sub-module's
 * top-level `register_*` calls fire as a side effect of being imported.
 * The shell then asks each registry by name — no further coupling.
 *
 * Adding a new bio surface = a new sub-module here + an import line
 * below. The platform never sees the new file.
 */
import './focusViews'
import './railIcons'
import './menuActions'
import './searchFacets'
import './homeTiles'

// Re-export the public registry APIs so platform components can import
// from `src/bio` directly (rather than from each sub-module).
export {
  register_focus_view,
  focus_view_for,
  registered_focus_view_types,
  type FocusViewProps,
} from './focusViews'

export {
  register_rail_icon,
  rail_icon_for,
  registered_rail_icons,
  type RailIconName,
  type RailIconProps,
} from './railIcons'

export {
  register_menu_traits,
  entity_menu_traits,
  is_pinnable,
  type EntityMenuTraits,
} from './menuActions'

export {
  register_search_facet,
  search_facets,
  search_placeholder,
} from './searchFacets'

export {
  register_home_tile,
  home_tiles,
  home_tile_for,
  type HomeTile,
} from './homeTiles'
