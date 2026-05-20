import { useState } from 'react'
import './App.css'
import Rail from './components/Rail'
import ProjectTree from './components/ProjectTree'
import ChatPane from './components/ChatPane'
import AdvisorRail from './components/AdvisorRail'
import FocusCanvas from './components/FocusCanvas'
import Home from './components/Home'
import { useChat } from './useChat'
import { useEntities } from './useEntities'

export default function App() {
  const [view, setView] = useState<'home' | 'workspace'>('home')
  const [focusedId, setFocusedId] = useState<string>('workspace')
  const { entities, refresh } = useEntities()
  const { messages, streaming, streamMsg, sendMessage } = useChat(focusedId, refresh)

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

  return (
    <div className="app">
      <Rail onEntitiesChanged={refresh} view={view} onNavigate={setView} />
      <ProjectTree
        entities={entities}
        focusedId={focusedId}
        onFocus={setFocusedId}
        onChange={refresh}
      />
      <div className="main">
        <FocusCanvas
          entity={focused}
          entities={entities}
          onChange={refresh}
          onFocus={setFocusedId}
        />
        <ChatPane
          messages={messages}
          streaming={streaming}
          streamMsg={streamMsg}
          onSend={sendMessage}
          focusedEntity={focused}
        />
      </div>
      <AdvisorRail focusedId={focusedId} focusedType={focused?.type} />
    </div>
  )
}
