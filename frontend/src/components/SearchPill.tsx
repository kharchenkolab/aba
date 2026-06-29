/**
 * SearchPill — the discoverable entry point for project-wide search, in the
 * canvas title-bar slot the Advisor strip used to occupy. Looks like a search
 * box; clicking anywhere on it opens the ⌘K palette (SearchModal).
 */
import './SearchPill.css'

const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

export default function SearchPill({ onOpen }: { onOpen: () => void }) {
  return (
    <button className="search-pill" onClick={onOpen} title="Search the project">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
      </svg>
      <span className="search-pill-label">Search…</span>
      <span className="search-pill-kbd">{IS_MAC ? '⌘K' : 'Ctrl K'}</span>
    </button>
  )
}
