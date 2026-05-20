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

export type RailIconName = 'brand' | 'home' | 'projects' | 'skills' | 'queues' | 'alerts'

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
  }
}
