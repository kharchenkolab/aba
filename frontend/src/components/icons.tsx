/**
 * Shared icon set, recovered from the original mockup (mockup/icons.svg).
 *
 * Agent identity: one distinct line-glyph per agent in its own colour, used
 * everywhere an agent appears (advisor rail, chat tabs, message avatars) so
 * they're instantly tellable apart. Glyphs inherit `currentColor`.
 */
import type { CSSProperties } from 'react'

export type AgentKey = 'guide' | 'methodologist' | 'skeptic' | 'explorer' | 'stylist'

export interface Agent {
  key: AgentKey
  name: string
  color: string   // CSS custom property reference
  status: string
}

export const AGENTS: Agent[] = [
  { key: 'guide',         name: 'Guide',         color: 'var(--guide)',    status: 'online' },
  { key: 'methodologist', name: 'Methodologist', color: 'var(--metho)',    status: 'on run' },
  { key: 'skeptic',       name: 'Skeptic',       color: 'var(--skeptic)',  status: 'on promote' },
  { key: 'explorer',      name: 'Explorer',      color: 'var(--explorer)', status: 'on data' },
  { key: 'stylist',       name: 'Stylist',       color: 'var(--stylist)',  status: 'on write' },
]

export function agentColor(key: AgentKey): string {
  return AGENTS.find(a => a.key === key)?.color ?? 'var(--text-3)'
}

/** Stroked entity/section glyphs, recovered from the original mockup sprite
 *  (mockup/icons.svg: i-db, i-thread, i-fig, i-doc, …). Replaces the old filled
 *  square paths so the rail matches the design screenshots. Inherits color. */
export function EntityGlyph({ name, size = 14, className }: { name: string; size?: number; className?: string }) {
  const svg = {
    className, width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: 1.6,
    strokeLinejoin: 'round' as const, strokeLinecap: 'round' as const,
  }
  switch (name) {
    case 'dataset':
    case 'db':
      return (
        <svg {...svg}>
          <ellipse cx="12" cy="6" rx="7" ry="2.4" />
          <path d="M5 6v6c0 1.3 3.1 2.4 7 2.4s7-1.1 7-2.4V6" />
          <path d="M5 12v6c0 1.3 3.1 2.4 7 2.4s7-1.1 7-2.4v-6" />
        </svg>
      )
    case 'thread':
      return <svg {...svg}><path d="M4 12a8 8 0 1 1 16 0c0 4-3 7-7 7H4l2-3a8 8 0 0 1-2-4Z" /></svg>
    case 'figure':
    case 'fig':
      return (
        <svg {...svg}>
          <path d="M4 19V5M4 19h16" />
          <path d="M7 16V11M11 16V8M15 16V12M19 16V7" />
        </svg>
      )
    case 'table':
      return (
        <svg {...svg}>
          <rect x="4" y="4" width="16" height="16" rx="1.5" />
          <path d="M4 9h16M4 14h16M10 4v16" />
        </svg>
      )
    case 'analysis':
    case 'run':
      return <svg {...svg}><path d="M5 4l5 4-5 4M12 14h7" /></svg>
    case 'result':   // stacked layers — a kept observation (one or more panels)
      return <svg {...svg}><path d="M12 3l9 5-9 5-9-5z" /><path d="M3 13l9 5 9-5" /></svg>
    case 'claim':
      return <svg {...svg}><path d="M12 3l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9 6.8 19.2l1-5.8L3.5 9.2l5.9-.9z" /></svg>
    case 'narrative':
    case 'doc':
      return (
        <svg {...svg}>
          <path d="M6 3h9l5 5v13H6z" />
          <path d="M14 3v6h6M9 13h6M9 17h6" />
        </svg>
      )
    default:
      return <svg {...svg}><circle cx="12" cy="12" r="8" /></svg>
  }
}

/** Distinct line-glyph per agent (paths from the original mockup). */
export function AgentGlyph({ agent, size = 15 }: { agent: AgentKey; size?: number }) {
  const svg = { width: size, height: size, viewBox: '0 0 24 24' }
  switch (agent) {
    case 'guide':         // radiant compass-star — the primary voice
      return (
        <svg {...svg}>
          <circle cx="12" cy="12" r="2" fill="currentColor" />
          <path d="M12 2v6M12 16v6M2 12h6M16 12h6M5 5l4 4M15 15l4 4M19 5l-4 4M9 15l-4 4"
                stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" fill="none" />
        </svg>
      )
    case 'methodologist': // linked nodes — method / structure
      return (
        <svg {...svg} fill="none" stroke="currentColor" strokeWidth="1.7">
          <circle cx="12" cy="7" r="3" />
          <circle cx="5" cy="17" r="2.4" />
          <circle cx="19" cy="17" r="2.4" />
          <path d="M9.7 9.4L7 14.8M14.3 9.4L17 14.8M7.5 17h9" strokeLinecap="round" />
        </svg>
      )
    case 'skeptic':       // magnifier — scrutiny
      return (
        <svg {...svg} fill="none" stroke="currentColor" strokeWidth="1.7">
          <circle cx="11" cy="11" r="6.5" />
          <line x1="16" y1="16" x2="21" y2="21" strokeLinecap="round" />
        </svg>
      )
    case 'explorer':      // compass-leaf — discovery
      return (
        <svg {...svg} fill="none" stroke="currentColor" strokeWidth="1.6">
          <circle cx="12" cy="12" r="9" />
          <path d="M8 14c3 1 7 1 8-3 0-1 0-3-2-4-1 0-3 1-4 2-2 1-3 3-2 5z" strokeLinejoin="round" />
          <line x1="8" y1="14" x2="11" y2="11" strokeLinecap="round" />
        </svg>
      )
    case 'stylist':       // pencil — writing
      return (
        <svg {...svg} fill="none" stroke="currentColor" strokeWidth="1.6">
          <path d="M4 20l4-1 11-11-3-3L5 16l-1 4z" strokeLinejoin="round" />
          <path d="M14 5l3 3" />
        </svg>
      )
  }
}

/** Agent avatar: the glyph in the agent's colour on a soft tinted disc. */
export function AgentAvatar({ agent, size = 24 }: { agent: AgentKey; size?: number }) {
  const color = agentColor(agent)
  const style: CSSProperties = {
    width: size, height: size,
    color,
    background: `color-mix(in srgb, ${color} 14%, #fff)`,
    boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${color} 26%, transparent)`,
  }
  return (
    <span className="agent-avatar" style={style}>
      <AgentGlyph agent={agent} size={Math.round(size * 0.64)} />
    </span>
  )
}

// ---------- Left-rail icons (from the mockup) ----------

export type RailIconName =
  | 'brand'
  | 'home'
  | 'projects'
  | 'skills'
  | 'queues'
  | 'alerts'
  | 'threads'
  | 'claims'
  | 'data'
  | 'runs'
  | 'files'

export function RailIcon({ name, size = 24 }: { name: RailIconName; size?: number }) {
  const svg = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
                stroke: 'currentColor' as const }
  switch (name) {
    case 'brand':
      return (
        <svg width={size} height={size} viewBox="0 0 32 32" fill="none" stroke="currentColor">
          <path d="M16 3l11 6v14l-11 6L5 23V9l11-6z" strokeWidth="1.7" strokeLinejoin="round" />
          <path d="M16 3v26M5 9l22 14M27 9L5 23" strokeWidth="1.3" opacity="0.6" />
        </svg>
      )
    case 'home':
      return (
        <svg {...svg}>
          <path d="M3 11L12 4l9 7v8a2 2 0 0 1-2 2h-3v-6h-8v6H5a2 2 0 0 1-2-2v-8Z"
                strokeWidth="1.8" strokeLinejoin="round" />
        </svg>
      )
    case 'projects':
      return (
        <svg {...svg}>
          <rect x="3" y="6" width="18" height="14" rx="2" strokeWidth="1.8" />
          <path d="M3 9h7l2-3h7" strokeWidth="1.8" strokeLinejoin="round" />
        </svg>
      )
    case 'skills':
      return (
        <svg {...svg}>
          <rect x="3" y="4" width="18" height="16" rx="2" strokeWidth="1.8" />
          <path d="M7 10l2 2 4-4M7 16h10" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )
    case 'queues':
      return (
        <svg {...svg}>
          <ellipse cx="12" cy="6" rx="8" ry="2.6" strokeWidth="1.8" />
          <path d="M4 6v5c0 1.4 3.6 2.6 8 2.6s8-1.2 8-2.6V6" strokeWidth="1.8" />
          <path d="M4 12v5c0 1.4 3.6 2.6 8 2.6s8-1.2 8-2.6V12" strokeWidth="1.8" />
        </svg>
      )
    case 'alerts':
      return (
        <svg {...svg}>
          <path d="M6 17V11a6 6 0 1 1 12 0v6l1.5 2.5h-15Z" strokeWidth="1.8" strokeLinejoin="round" />
          <path d="M10 21a2 2 0 0 0 4 0" strokeWidth="1.8" />
        </svg>
      )
    case 'threads':
      return (
        <svg {...svg}>
          <path d="M21 15a4 4 0 0 1-4 4H8l-5 4v-8a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z" strokeWidth="1.8" strokeLinejoin="round" />
          <path d="M8 11V6a3 3 0 0 1 3-3h7a3 3 0 0 1 3 3v7" strokeWidth="1.8" strokeLinejoin="round" />
        </svg>
      )
    case 'claims':
      return (
        <svg {...svg}>
          <path d="M9 11l3 3L22 4" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" strokeWidth="1.8" strokeLinejoin="round" />
        </svg>
      )
    case 'data':
      return (
        <svg {...svg}>
          <ellipse cx="12" cy="5" rx="8" ry="3" strokeWidth="1.8" />
          <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" strokeWidth="1.8" />
          <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" strokeWidth="1.8" />
        </svg>
      )
    case 'runs':
      return (
        <svg {...svg}>
          <path d="M5 3l14 9-14 9V3Z" strokeWidth="1.8" strokeLinejoin="round" />
        </svg>
      )
    case 'files':
      return (
        <svg {...svg}>
          <path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" strokeWidth="1.8" strokeLinejoin="round" />
        </svg>
      )
  }
}
