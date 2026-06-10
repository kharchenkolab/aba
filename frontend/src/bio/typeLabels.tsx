/**
 * Display-label registry — bio side of the platform/bio split.
 *
 * The shell needs a human-readable label for an entity type ("Run" for
 * `analysis`, "Section" for `narrative`, …). The canonical source is
 * the `display` field in entity_types/*.yaml (the backend catalog,
 * mirrored to entityTypes.ts), but until that catalog loads the
 * platform falls back to this static registry — so the chrome around
 * a freshly-loaded page doesn't briefly read "Entity" before the
 * fetch completes.
 *
 * Bio types each register their display label on module load; the
 * shell calls `type_label_for(name)` (or its `type_label_or_fallback`
 * sibling) and gets a stable answer regardless of catalog state.
 */
import { typeOf } from '../entityTypes'

const _LABELS = new Map<string, string>()

/** Register (or override) the display label for a type. */
export function register_type_label(type: string, label: string): void {
  _LABELS.set(type, label)
}

/** Look up the display label for a type. Prefers the loaded entity-type
 *  catalog when available, falls back to the static registry below.
 *  Returns null when the type is unknown to BOTH the catalog and the
 *  static registry — callers should pick a generic default ("Entity"). */
export function type_label_for(type: string | undefined | null): string | null {
  if (!type) return null
  const fromCatalog = typeOf(type)?.display
  if (fromCatalog) return fromCatalog
  return _LABELS.get(type) ?? null
}

/** Convenience: type_label_for(type) ?? fallback. */
export function type_label_or_fallback(type: string | undefined | null, fallback: string = 'Entity'): string {
  return type_label_for(type) ?? fallback
}


// ---------- Default bio type labels ----------
// Mirror the existing typeLabel/entityLabel switches in App.tsx.

register_type_label('figure',    'Figure')
register_type_label('table',     'Table')
register_type_label('finding',   'Finding')
register_type_label('result',    'Result')
register_type_label('dataset',   'Dataset')
register_type_label('narrative', 'Section')
register_type_label('analysis',  'Run')
register_type_label('claim',     'Claim')
register_type_label('thread',    'Thread')
register_type_label('note',      'Note')
register_type_label('workspace', 'Workspace')
register_type_label('cell',      'Cell')
register_type_label('plan',      'Plan')
