import { useState } from 'react'
import './App.css'
import Rail from './components/Rail'
import ProjectTree from './components/ProjectTree'
import ChatPane from './components/ChatPane'
import AdvisorRail from './components/AdvisorRail'
import FocusCanvas from './components/FocusCanvas'
import Home from './components/Home'
import VResizer from './components/VResizer'
import HResizer from './components/HResizer'
import { useChat } from './useChat'
import { useEntities } from './useEntities'

const FOCUS_DEFAULT = 320
const TREE_DEFAULT = 240
const TREE_MIN = 150
// Tallest the focus panel can grow — leaves a strip of chat (tabs + composer)
// visible so "maximize figure" doesn't fully hide the conversation.
const focusMax = () => Math.round(window.innerHeight * 0.82)

export default function App() {
  const [view, setView] = useState<'home' | 'workspace'>('home')
  const [focusedId, setFocusedId] = useState<string>('workspace')
  const [focusH, setFocusH] = useState(FOCUS_DEFAULT)
  const [treeW, setTreeW] = useState(TREE_DEFAULT)
  const [treeCollapsed, setTreeCollapsed] = useState(false)
  const [advisorW, setAdvisorW] = useState(260)
  const [advisorCollapsed, setAdvisorCollapsed] = useState(false)
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
  const { entities, refresh } = useEntities()
  const { messages, streaming, streamMsg, sendMessage, retryLast } = useChat(
    focusedId, refresh, annotation,
  )

  const focused = entities.find(e => e.id === focusedId) ?? null

  if (view === 'home') {
    return (
      <div className="app app--home">
        <Rail onEntitiesChanged={refresh} view={view} onNavigate={setView} />
        <Home
          onOpenWorkspace={() => setView('workspace')}
          onEntitiesChanged={refresh}
          onFocus={setFocusedId}
        />
      </div>
    )
  }

  const gridCols =
    `var(--w-rail) ${treeCollapsed ? 0 : treeW}px 14px 1fr 14px ${advisorCollapsed ? 0 : advisorW}px`

  return (
    <div className="app app--workspace" style={{ gridTemplateColumns: gridCols }}>
      <Rail onEntitiesChanged={refresh} view={view} onNavigate={setView} />
      {treeCollapsed ? <div /> : (
        <ProjectTree
          entities={entities}
          focusedId={focusedId}
          onFocus={setFocusedId}
          onChange={refresh}
        />
      )}
      <HResizer
        collapsed={treeCollapsed}
        onDrag={dx => setTreeW(w => Math.min(440, Math.max(TREE_MIN, w + dx)))}
        onToggle={() => setTreeCollapsed(c => !c)}
      />
      <div className="main">
        <div className="focus-wrap" style={{ height: focusH }}>
          <FocusCanvas
            entity={focused}
            entities={entities}
            onChange={refresh}
            onFocus={setFocusedId}
            onAnnotate={attachAnnotation}
            annotClear={annotClear}
          />
        </div>
        <VResizer
          state={focusH < 8 ? 'chat' : focusH >= focusMax() - 6 ? 'figure' : 'mid'}
          onDrag={dy => setFocusH(h => Math.min(focusMax(), Math.max(0, h + dy)))}
          onMaxFigure={() => setFocusH(focusMax())}
          onMaxChat={() => setFocusH(0)}
          onRestore={() => setFocusH(FOCUS_DEFAULT)}
        />
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
        />
      </div>
      <HResizer
        side="right"
        collapsed={advisorCollapsed}
        onDrag={dx => setAdvisorW(w => Math.min(420, Math.max(180, w - dx)))}
        onToggle={() => setAdvisorCollapsed(c => !c)}
      />
      {advisorCollapsed ? <div /> : (
        <AdvisorRail focusedId={focusedId} focusedType={focused?.type} onTry={setPrefill} onFocus={setFocusedId} />
      )}
    </div>
  )
}
