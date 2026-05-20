import { useState, useEffect } from 'react'
import './App.css'
import Rail from './components/Rail'
import ProjectTree from './components/ProjectTree'
import ChatPane from './components/ChatPane'
import AdvisorStrip from './components/AdvisorStrip'
import FocusCanvas from './components/FocusCanvas'
import Home from './components/Home'
import HResizer from './components/HResizer'
import PostureToggle, { type Posture } from './components/PostureToggle'
import SearchModal from './components/SearchModal'
import { useChat } from './useChat'
import { useEntities } from './useEntities'
import type { Entity } from './types'

const TREE_DEFAULT = 240
const TREE_MIN = 150

function entityLabel(e: Entity | null): string {
  switch (e?.type) {
    case 'figure': return 'Figure'
    case 'table': return 'Table'
    case 'finding': return 'Finding'
    case 'result': return 'Result'
    case 'dataset': return 'Dataset'
    case 'narrative': return 'Section'
    default: return 'Entity'
  }
}

export default function App() {
  const [view, setView] = useState<'home' | 'workspace'>('home')
  const [focusedId, setFocusedId] = useState<string>('workspace')
  const [posture, setPosture] = useState<Posture>('chat')
  const [treeW, setTreeW] = useState(TREE_DEFAULT)
  const [treeCollapsed, setTreeCollapsed] = useState(false)
  const [prefill, setPrefill] = useState('')
  const [composerFocus, setComposerFocus] = useState(0)
  const [annotClear, setAnnotClear] = useState(0)
  const attachAnnotation = (a: { image: string; note: string }) => {
    setAnnotation(a)
    setComposerFocus(n => n + 1)   // jump the cursor to the composer
  }
  const clearAnnotation = () => {
    setAnnotation(null)
    setAnnotClear(n => n + 1)      // erase the drawn mark on the figure too
  }
  const [annotation, setAnnotation] = useState<{ image: string; note: string } | null>(null)
  const [searchOpen, setSearchOpen] = useState(false)
  const { entities, refresh } = useEntities()

  // Cmd/Ctrl-K opens fallback search.
  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault(); setSearchOpen(o => !o)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])
  const { messages, streaming, streamMsg, sendMessage, retryLast } = useChat(
    focusedId, refresh, annotation,
  )

  const focused = entities.find(e => e.id === focusedId) ?? null
  const scoped = !!focused && focused.type !== 'workspace'

  // Single-click focuses an artifact (peek in chat-first). Focusing the
  // workspace root opens entity-first — that's where workspace-level actions
  // (add data, new manuscript section) live. "open" promotes any entity.
  const focus = (id: string) => {
    setFocusedId(id)
    if (id === 'workspace') setPosture('entity')
  }
  const openEntity = (id: string) => { setFocusedId(id); setPosture('entity') }

  // Pin/unpin a figure entity to keep it in the project (chat capture).
  const pinEntity = (id: string, pinned: boolean) => {
    fetch(`/api/entities/${encodeURIComponent(id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pinned }),
    }).then(() => refresh()).catch(() => {})
  }
  // Keep any (non-entity) message as a snapshot note, keyed by content.
  const keepMessage = (key: string, text: string, image_urls: string[]) => {
    fetch('/api/messages/pin', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, text, image_urls }),
    }).then(() => refresh()).catch(() => {})
  }
  // Keys of currently-kept message notes (to reflect pin state in chat).
  // Only active notes count — an archived (unpinned) note must not keep the
  // chat pin button lit.
  const keptKeys = new Set(
    entities
      .filter(e => e.type === 'note' && e.status === 'active' && (e.metadata?.source_key as string))
      .map(e => e.metadata!.source_key as string),
  )

  if (view === 'home') {
    return (
      <div className="app app--home">
        <Rail onEntitiesChanged={refresh} view={view} onNavigate={setView} />
        <Home
          onOpenWorkspace={() => setView('workspace')}
          onEntitiesChanged={refresh}
          onFocus={openEntity}
        />
      </div>
    )
  }

  const gridCols = `var(--w-rail) ${treeCollapsed ? 0 : treeW}px 14px 1fr`

  const chatPane = (compact: boolean) => (
    <ChatPane
      messages={messages}
      streaming={streaming}
      streamMsg={streamMsg}
      onSend={sendMessage}
      focusedEntity={focused}
      annotation={annotation}
      onClearAnnotation={clearAnnotation}
      prefill={prefill}
      onPrefillConsumed={() => setPrefill('')}
      composerFocus={composerFocus}
      onAnnotate={attachAnnotation}
      annotClear={annotClear}
      onRetry={retryLast}
      embedded
      compact={compact}
      entities={entities}
      onPin={pinEntity}
      keptKeys={keptKeys}
      onKeepMessage={(key, text, image_urls) => keepMessage(key, text, image_urls)}
    />
  )

  const entityPanel = (primary: boolean) => (
    <div className={`surface-panel entity-surface ${primary ? 'primary' : ''}`}>
      <FocusCanvas
        entity={focused}
        entities={entities}
        onChange={refresh}
        onFocus={openEntity}
        onAnnotate={attachAnnotation}
        annotClear={annotClear}
        compact={!primary}
      />
    </div>
  )

  const peekEmpty = (
    <div className="surface-panel peek-empty">
      <p>Pick an artifact from the tree to preview it here, or switch to the
        <button className="link-btn" onClick={() => setPosture('entity')}> workspace view</button>.</p>
    </div>
  )

  return (
    <div className="app app--workspace" style={{ gridTemplateColumns: gridCols }}>
      <Rail onEntitiesChanged={refresh} view={view} onNavigate={setView} />
      {treeCollapsed ? <div /> : (
        <ProjectTree
          entities={entities}
          focusedId={focusedId}
          onFocus={focus}
          onChange={refresh}
        />
      )}
      <HResizer
        collapsed={treeCollapsed}
        onDrag={dx => setTreeW(w => Math.min(440, Math.max(TREE_MIN, w + dx)))}
        onToggle={() => setTreeCollapsed(c => !c)}
      />

      <div className="canvas">
        <div className="canvas-head">
          <div className="canvas-title">
            {scoped
              ? <><span className="canvas-title__type">{focused!.type}</span>{focused!.title}</>
              : <>Workspace</>}
          </div>
          <div className="canvas-actions">
            <AdvisorStrip focusedId={focusedId} focusedType={focused?.type}
                          onTry={setPrefill} onFocus={focus} />
            <PostureToggle posture={posture} onChange={setPosture} entityLabel={entityLabel(focused)} />
          </div>
        </div>

        <div className="canvas-body">
          <div className={`split split--${posture}`}>
            {posture === 'chat' ? (
              <>
                <div className="surface-panel primary chat-primary">{chatPane(false)}</div>
                {scoped ? entityPanel(false) : peekEmpty}
              </>
            ) : (
              <>
                {entityPanel(true)}
                <div className="surface-panel chat-peek">{chatPane(true)}</div>
              </>
            )}
          </div>
        </div>
      </div>

      <SearchModal open={searchOpen} onClose={() => setSearchOpen(false)}
                   onPick={openEntity} />
    </div>
  )
}
