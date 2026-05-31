/**
 * Run-output browser (v1). Lists a run's individual outputs in-app — plots as a
 * thumbnail grid, files/tables as rows — each with two per-item gestures:
 *   ★  pin   — keep it as a Result in the thread (evidence)
 *   💬 chat  — bring it into the Guide chat, focusing attention on it
 * Later this can grow into a real filesystem / type-specific browser; the item
 * shape and the two gestures stay the same.
 */
import { useEffect, useState } from 'react'
import { EntityGlyph } from './icons'
import HighlightableImage from './HighlightableImage'
import './ResultList.css'

export interface OutputItem {
  kind: string                 // figure | table | file | view | dataset
  label: string
  thumb?: string               // image url for plot previews
  href?: string                // external link (browse / open)
  size?: string
  role?: 'primary' | 'diagnostic' | 'bulk'  // tier; absent ⇒ diagnostic
}

function PinChat({ item, onPin, onChat }: { item: OutputItem; onPin: (i: OutputItem) => void; onChat: (i: OutputItem) => void }) {
  return (
    <span className="rl-acts">
      <button className="rl-act" title="Pin as a result (evidence)" onClick={e => { e.stopPropagation(); e.preventDefault(); onPin(item) }}>
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 17v5M9 3h6l-1 7 3 3H7l3-3z"/></svg>
      </button>
      <button className="rl-act" title="Discuss with Guide" onClick={e => { e.stopPropagation(); e.preventDefault(); onChat(item) }}>
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
      </button>
    </span>
  )
}

export default function ResultList({ items, runId, browse, bulk, onPin, onChat, onChatAnnotated, onRegister }: {
  items: OutputItem[]
  runId?: string
  browse?: { label: string; href: string }
  bulk?: { count: number; note?: string; href?: string }
  onPin: (i: OutputItem) => void
  onChat: (i: OutputItem) => void
  onChatAnnotated?: (i: OutputItem, annotation: { image: string; note: string }) => void
  onRegister?: (i: OutputItem) => void
}) {
  const primaries = items.filter(i => i.role === 'primary')
  const rest = items.filter(i => i.role !== 'primary' && i.role !== 'bulk')
  const plots = rest.filter(i => i.kind === 'figure' || i.kind === 'view')
  const tables = rest.filter(i => i.kind === 'table')
  const files = rest.filter(i => i.kind === 'file')
  const tiered = primaries.length > 0
  const [preview, setPreview] = useState<OutputItem | null>(null)
  const [bulkOpen, setBulkOpen] = useState(false)

  return (
    <div className="rl">
      {primaries.map((p, i) => (
        <div key={`p${i}`} className="rl-primary">
          <EntityGlyph className="rl-primary__icon" name="dataset" size={18} />
          <div className="rl-primary__text">
            <div className="rl-primary__label">{p.label}</div>
            <div className="rl-primary__sub">{[p.size, 'reference'].filter(Boolean).join(' · ')}</div>
          </div>
          {onRegister && <button className="rl-primary__act" onClick={() => onRegister(p)}>Register as dataset</button>}
          {p.href && <a className="rl-primary__open" href={p.href} target="_blank" rel="noreferrer">Open ↗</a>}
        </div>
      ))}

      {plots.length > 0 && (
        <div className="rl-group">
          <div className="rl-group__head">{tiered ? 'Diagnostics' : 'Plots'} <span className="rl-group__n">{plots.length}</span></div>
          <div className="rl-grid">
            {plots.map((p, i) => (
              <div key={i} className="rl-plot">
                <div className="rl-plot__frame" onClick={() => setPreview(p)} title="Click to preview">
                  {p.thumb && /\.(png|jpe?g|svg|webp|gif)$/i.test(p.thumb)
                    ? <img className="rl-plot__img" src={p.thumb} alt={p.label} loading="lazy" />
                    : <span className="rl-plot__noimg">
                        <EntityGlyph name="figure" size={18} />
                        {/* Non-image figures (PDF, etc.): show the extension as a small badge
                            so the user knows what's there — img tag can't render PDFs inline. */}
                        {p.thumb && /\.(pdf|html?|tif{1,2}f?)$/i.test(p.thumb) && (
                          <span style={{ fontSize: 9, fontWeight: 700, opacity: 0.7, marginTop: 4, textTransform: 'uppercase' }}>
                            {(p.thumb.match(/\.([a-z]+)$/i) || [, ''])[1]}
                          </span>
                        )}
                      </span>}
                  <PinChat item={p} onPin={onPin} onChat={onChat} />
                </div>
                <div className="rl-plot__label" title={p.label}>{p.label}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {(tables.length > 0 || files.length > 0) && (
        <div className="rl-group">
          <div className="rl-group__head">Files <span className="rl-group__n">{tables.length + files.length}</span></div>
          <div className="rl-rows">
            {[...tables, ...files].map((f, i) => {
              const inner = (
                <>
                  <EntityGlyph className="rl-row__icon" name={f.kind === 'table' ? 'table' : 'doc'} size={14} />
                  <span className="rl-row__label">{f.label}</span>
                  {f.size && <span className="rl-row__size">{f.size}</span>}
                  {f.href && <span className="rl-row__ext">↗</span>}
                  <PinChat item={f} onPin={onPin} onChat={onChat} />
                </>
              )
              return f.href
                ? <a key={i} className="rl-row" href={f.href} target="_blank" rel="noreferrer">{inner}</a>
                : <div key={i} className="rl-row rl-row--static">{inner}</div>
            })}
          </div>
        </div>
      )}

      {bulk && (
        <div className="rl-bulk">
          <button className="rl-bulk__head" onClick={() => setBulkOpen(o => !o)}>
            <span className="rl-bulk__chev">{bulkOpen ? '▾' : '▸'}</span>
            All files <span className="rl-group__n">{bulk.count}</span>
          </button>
          {bulkOpen && (
            <div className="rl-bulk__body">
              {bulk.note && <div className="rl-bulk__note">{bulk.note}</div>}
              {bulk.href
                ? <a className="rl-browse" href={bulk.href} target="_blank" rel="noreferrer">Browse all files ↗</a>
                : <div className="rl-bulk__note rl-bulk__note--dim">In-app file browser coming soon — pin individual files from here.</div>}
            </div>
          )}
        </div>
      )}

      {browse && (
        <a className="rl-browse" href={browse.href} target="_blank" rel="noreferrer">
          Browse all outputs — {browse.label} ↗
        </a>
      )}

      {preview && (
        <OutputPreview item={preview} runId={runId} onPin={onPin} onChat={onChat}
          onChatAnnotated={onChatAnnotated} onClose={() => setPreview(null)} />
      )}
    </div>
  )
}

/**
 * Full-res preview modal. The image is highlightable (marker/box → composite
 * attached to chat). Chat/highlight close the modal and focus the composer (so
 * the chat isn't trapped behind it). "Detach" pops the plot into a real, separate
 * browser window — the user can park it on another monitor and keep working.
 */
function OutputPreview({ item, runId, onPin, onChat, onChatAnnotated, onClose }: {
  item: OutputItem
  runId?: string
  onPin: (i: OutputItem) => void
  onChat: (i: OutputItem) => void
  onChatAnnotated?: (i: OutputItem, annotation: { image: string; note: string }) => void
  onClose: () => void
}) {
  const [marking, setMarking] = useState(false)
  const [mode, setMode] = useState<'highlight' | 'box'>('highlight')
  const [clearSig, setClearSig] = useState(0)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const doChat = () => { onChat(item); onClose() }
  const doHighlight = (a: { image: string; note: string }) => { onChatAnnotated?.(item, a); onClose() }
  const pickMode = (m: 'highlight' | 'box') => { setMode(m); setClearSig(s => s + 1) }

  // Detach into a REAL same-origin browser window (a hash route into this app),
  // so it has a normal URL (no about:blank) and renders working controls. The
  // payload goes through a short localStorage token to keep the URL clean; the
  // window posts pin/chat/highlight back to us. Then close the modal.
  const detachToWindow = () => {
    if (!item.thumb) return
    const id = Date.now().toString(36)
    try { localStorage.setItem(`aba-preview-${id}`, JSON.stringify({ ...item, runId })) } catch { /* ignore */ }
    const url = `${window.location.origin}${window.location.pathname}#preview=${id}`
    window.open(url, `aba-preview-${id}`, 'popup=yes,width=940,height=820')
    onClose()
  }

  return (
    <div className="rl-modal" onClick={onClose}>
      <div className="rl-modal__box" onClick={e => e.stopPropagation()}>
        <div className="rl-modal__head">
          {item.thumb && (
            <button className="rl-modal__btn" onClick={detachToWindow} title="Open in a separate browser window">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="13" height="13" rx="1.5" /><path d="M8 8h13v13H8" />
              </svg>
            </button>
          )}
          <span className="rl-modal__title">{item.label}</span>
          {marking && (
            <>
              <button className={`rl-act rl-act--sm ${mode === 'highlight' ? 'is-sel' : ''}`} title="Freehand marker" onClick={() => pickMode('highlight')}>
                <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M4 18 Q9 6 14 13 T20 8"/></svg>
              </button>
              <button className={`rl-act rl-act--sm ${mode === 'box' ? 'is-sel' : ''}`} title="Box" onClick={() => pickMode('box')}>
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.4"><rect x="4" y="6" width="16" height="12" rx="1.5"/></svg>
              </button>
              <button className="rl-act rl-act--sm" title="Clear mark" onClick={() => setClearSig(s => s + 1)}>
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
              </button>
              <span className="rl-modal__sep" />
            </>
          )}
          {item.thumb && (
            <button className={`rl-act rl-act--hl ${marking ? 'is-on' : ''}`} title="Highlight a region to ask the Guide about" onClick={() => setMarking(m => !m)}>
              <svg viewBox="0 0 24 24" width="14" height="14" fill="#fde047" stroke="#ca8a04" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M15.6 2.6a2 2 0 012.8 0l3 3a2 2 0 010 2.8l-9 9-5.2 1.2 1.2-5.2 9-9zM5 19h14v2H5z"/></svg>
            </button>
          )}
          <button className="rl-act" title="Pin as a result (evidence)" onClick={() => onPin(item)}>
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 17v5M9 3h6l-1 7 3 3H7l3-3z"/></svg>
          </button>
          <button className="rl-act" title="Discuss with Guide" onClick={doChat}>
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          </button>
          {item.href && <a className="rl-modal__open" href={item.href} target="_blank" rel="noreferrer">Open ↗</a>}
          <button className="rl-modal__x" onClick={onClose} title="Close">×</button>
        </div>
        <div className="rl-modal__body">
          {item.thumb
            ? <HighlightableImage src={item.thumb} label={item.label} onAttach={doHighlight} className="rl-modal__img"
                                  marking={marking} onMarkingChange={setMarking} mode={mode} onModeChange={setMode}
                                  clearMarkSignal={clearSig} hideToolbar showToggle={false} />
            : <div className="rl-modal__noimg">No preview available for this output.</div>}
        </div>
      </div>
    </div>
  )
}
