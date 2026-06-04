/**
 * SearchInput — a small, self-contained filter box (magnifier glyph + input +
 * clear button). Used by the left-rail section lists (Threads/Data/Claims/…) to
 * narrow a list by name. Mirrors the affordance the Files tab already has via
 * its own <FileBrowser> search; kept generic so any list can reuse it.
 */
import './SearchInput.css'

interface Props {
  value: string
  onChange: (v: string) => void
  /** Required — the box has no visible label of its own. */
  ariaLabel: string
  placeholder?: string
}

export default function SearchInput({ value, onChange, ariaLabel, placeholder = 'Filter…' }: Props) {
  return (
    <div className="list-search">
      <MagnifierGlyph />
      <input
        type="text"
        className="list-search__input"
        placeholder={placeholder}
        value={value}
        onChange={e => onChange(e.target.value)}
        aria-label={ariaLabel}
        spellCheck={false}
      />
      {value && (
        <button className="list-search__clear" onClick={() => onChange('')} title="Clear filter" aria-label="Clear filter">×</button>
      )}
    </div>
  )
}

function MagnifierGlyph() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.5 10.5L13.5 13.5" />
    </svg>
  )
}
