/**
 * Bio aggregator — single entry point the app imports to ensure all
 * bio-side registrations have fired against the lib/ registry hubs.
 *
 * App.tsx does `import './bio'` once on startup. The side-effect chain:
 *   1. importing this module loads each lib/ registry file
 *   2. each lib/ file runs its top-level `register_*` calls
 *   3. afterwards `rail_icon_for(...)`, `entity_menu_traits(...)`,
 *      `home_tiles()`, etc. return the bio defaults.
 *
 * The platform/ shell reads registries directly from lib/ — never
 * from this file or from bio/ — so the platform-imports lint test
 * (src/platform/__platform_imports.test.ts) stays green.
 *
 * Re-exports below let bio components (focusViews, FocusCanvas
 * adapter) and App.tsx itself read registry APIs via `from './bio'`
 * — saving them from listing every lib/ path.
 */

// Side-effect imports — each lib/ module's top-level `register_*`
// calls populate its registry. focusViews stays in bio/ because the
// FocusViewProps shape is part of the bio contract.
import './focusViews'
import '../lib/railIcons'
import '../lib/menuActions'
import '../lib/searchFacets'
import '../lib/homeTiles'
import '../lib/typeLabels'
import '../lib/entityClasses'
import '../lib/sectionCounts'
import '../lib/projectSignals'
import './messageRendererDefault'

// Re-exports — the public registry API surface, gathered in one place.
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
} from '../lib/railIcons'

export {
  register_menu_traits,
  entity_menu_traits,
  is_pinnable,
  type EntityMenuTraits,
} from '../lib/menuActions'

export {
  register_search_facet,
  search_facets,
  search_placeholder,
} from '../lib/searchFacets'

export {
  register_home_tile,
  home_tiles,
  home_tile_for,
  register_card_order,
  card_order,
  type HomeTile,
} from '../lib/homeTiles'

export {
  register_type_label,
  type_label_for,
  type_label_or_fallback,
} from '../lib/typeLabels'

export {
  register_entity_class,
  type_in_class,
  types_in_class,
} from '../lib/entityClasses'

export {
  register_section_count,
  section_count,
  section_counts,
  type SectionName,
} from '../lib/sectionCounts'

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
} from '../lib/projectSignals'

export {
  register_message_renderer,
  message_renderer,
} from '../lib/messageRenderer'
