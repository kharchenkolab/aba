/**
 * Rail-icon registry — bio side of the platform/bio split (Wave 2 §6.3 C1).
 *
 * The platform shell (Rail.tsx, ProjectTree.tsx) needs an icon for each
 * project navigation entry and entity-glyph slot, but the SHAPES of those
 * icons are biology-flavored (dataset = cylinder, claim = pennant, figure
 * = bars, etc). Moving the switch-on-name into bio lets the shell stay
 * generic — the renderer asks `rail_icon_for(name)` and renders whatever
 * the bio cap registered, with a small "fallback circle" if nothing is.
 *
 * Today this just wraps the existing `RailIcon` switch from components/
 * icons.tsx so the migration is mechanical. A future bio domain could
 * register entirely different glyphs without editing the shell.
 */
import { useState } from 'react'
import type { ComponentType } from 'react'

export type RailIconName =
  | 'brand'
  | 'home'
  | 'projects'
  | 'queues'
  | 'alerts'
  | 'threads'
  | 'claims'
  | 'data'
  | 'runs'
  | 'files'
  | 'results'

export interface RailIconProps {
  size?: number
}

type Renderer = ComponentType<RailIconProps>

const _MAP = new Map<string, Renderer>()

/** Register a rail-icon renderer for the given name. Last registration wins. */
export function register_rail_icon(name: string, c: Renderer): void {
  _MAP.set(name, c)
}

/** Look up the registered renderer for `name`. Returns null when nothing is
 *  registered — callers should render a fallback (a circle, or nothing). */
export function rail_icon_for(name: string): Renderer | null {
  return _MAP.get(name) ?? null
}

/** Snapshot of all currently registered icon names. */
export function registered_rail_icons(): string[] {
  return [..._MAP.keys()]
}


// ---------- Default bio rail-icon set ----------
// Each function below is a single-shape stroked SVG. The switch in
// components/icons.tsx is the source — moving it into named functions
// here lets us register them by name + use the same set elsewhere.

const baseSvg = { fill: 'none' as const, stroke: 'currentColor' as const,
                  viewBox: '0 0 24 24', strokeLinejoin: 'round' as const,
                  strokeLinecap: 'round' as const }


// Branding override: when VITE_BRAND_LOGO is set, the rail's top-left
// mark renders that URL instead of the default hexagon. The URL is
// browser-resolved (no Vite static-import dance) so the file can live
// anywhere — typically `public/branding/<name>.png` (served at
// `/branding/<name>.png`) or an absolute external URL. Image-load
// errors flip to the hexagon — a repo without the operator's asset
// still renders cleanly.
function BrandIcon({ size = 24 }: RailIconProps) {
  const url = import.meta.env.VITE_BRAND_LOGO as string | undefined
  const [errored, setErrored] = useState(false)
  if (url && !errored) {
    return (
      <img src={url} alt="" width={size} height={size}
           style={{ display: 'block' }}
           onError={() => setErrored(true)} />
    )
  }
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" stroke="currentColor">
      <path d="M16 3l11 6v14l-11 6L5 23V9l11-6z" strokeWidth="1.7" strokeLinejoin="round" />
      <path d="M16 3v26M5 9l22 14M27 9L5 23" strokeWidth="1.3" opacity="0.6" />
    </svg>
  )
}

function HomeIcon({ size = 24 }: RailIconProps) {
  return (
    <svg width={size} height={size} {...baseSvg}>
      <path d="M3 11L12 4l9 7v8a2 2 0 0 1-2 2h-3v-6h-8v6H5a2 2 0 0 1-2-2v-8Z"
            strokeWidth="1.8" />
    </svg>
  )
}

function ProjectsIcon({ size = 24 }: RailIconProps) {
  return (
    <svg width={size} height={size} {...baseSvg}>
      <rect x="3" y="6" width="18" height="14" rx="2" strokeWidth="1.8" />
      <path d="M3 9h7l2-3h7" strokeWidth="1.8" />
    </svg>
  )
}

function QueuesIcon({ size = 24 }: RailIconProps) {
  return (
    <svg width={size} height={size} {...baseSvg}>
      <ellipse cx="12" cy="6" rx="8" ry="2.6" strokeWidth="1.8" />
      <path d="M4 6v5c0 1.4 3.6 2.6 8 2.6s8-1.2 8-2.6V6" strokeWidth="1.8" />
      <path d="M4 12v5c0 1.4 3.6 2.6 8 2.6s8-1.2 8-2.6V12" strokeWidth="1.8" />
    </svg>
  )
}

function AlertsIcon({ size = 24 }: RailIconProps) {
  return (
    <svg width={size} height={size} {...baseSvg}>
      <path d="M6 17V11a6 6 0 1 1 12 0v6l1.5 2.5h-15Z" strokeWidth="1.8" />
      <path d="M10 21a2 2 0 0 0 4 0" strokeWidth="1.8" />
    </svg>
  )
}

function ThreadsIcon({ size = 24 }: RailIconProps) {
  return (
    <svg width={size} height={size} {...baseSvg}>
      <path d="M21 15a4 4 0 0 1-4 4H8l-5 4v-8a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z" strokeWidth="1.8" />
      <path d="M8 11V6a3 3 0 0 1 3-3h7a3 3 0 0 1 3 3v7" strokeWidth="1.8" />
    </svg>
  )
}

function ClaimsIcon({ size = 24 }: RailIconProps) {
  return (
    <svg width={size} height={size} {...baseSvg}>
      <path d="M9 11l3 3L22 4" strokeWidth="1.8" />
      <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" strokeWidth="1.8" />
    </svg>
  )
}

function DataIcon({ size = 24 }: RailIconProps) {
  return (
    <svg width={size} height={size} {...baseSvg}>
      <ellipse cx="12" cy="5" rx="8" ry="3" strokeWidth="1.8" />
      <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" strokeWidth="1.8" />
      <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" strokeWidth="1.8" />
    </svg>
  )
}

function RunsIcon({ size = 24 }: RailIconProps) {
  return (
    <svg width={size} height={size} {...baseSvg}>
      <path d="M5 3l14 9-14 9V3Z" strokeWidth="1.8" />
    </svg>
  )
}

function FilesIcon({ size = 24 }: RailIconProps) {
  return (
    <svg width={size} height={size} {...baseSvg}>
      <path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" strokeWidth="1.8" />
    </svg>
  )
}

function ResultsIcon({ size = 24 }: RailIconProps) {
  // Stacked layers — mirrors the EntityGlyph 'result' shape.
  return (
    <svg width={size} height={size} {...baseSvg}>
      <path d="M12 3l9 5-9 5-9-5z" strokeWidth="1.7" />
      <path d="M3 13l9 5 9-5" strokeWidth="1.7" />
    </svg>
  )
}


register_rail_icon('brand', BrandIcon)
register_rail_icon('home', HomeIcon)
register_rail_icon('projects', ProjectsIcon)
register_rail_icon('queues', QueuesIcon)
register_rail_icon('alerts', AlertsIcon)
register_rail_icon('threads', ThreadsIcon)
register_rail_icon('claims', ClaimsIcon)
register_rail_icon('data', DataIcon)
register_rail_icon('runs', RunsIcon)
register_rail_icon('files', FilesIcon)
register_rail_icon('results', ResultsIcon)
