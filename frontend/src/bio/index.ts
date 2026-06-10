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
import './typeLabels'
import './entityClasses'
import './sectionCounts'
import './projectSignals'

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
  register_card_order,
  card_order,
  type HomeTile,
} from './homeTiles'

export {
  register_type_label,
  type_label_for,
  type_label_or_fallback,
} from './typeLabels'

export {
  register_entity_class,
  type_in_class,
  types_in_class,
} from './entityClasses'

export {
  register_section_count,
  section_count,
  section_counts,
  type SectionName,
} from './sectionCounts'

export {
  dataset_count,
  has_any_dataset,
  has_pinned_figure,
  has_user_question,
  kept_message_keys,
  pinned_figure_ids,
  default_pin_kind,
  uses_claim_focus_route,
  supports_focused_highlighting,
} from './projectSignals'
