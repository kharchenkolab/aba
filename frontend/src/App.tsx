import { useState } from 'react'
import './App.css'
import Rail from './components/Rail'
import ProjectTree from './components/ProjectTree'
import ChatPane from './components/ChatPane'
import AdvisorRail from './components/AdvisorRail'
import FocusCanvas from './components/FocusCanvas'
import { useChat } from './useChat'
import { useEntities } from './useEntities'

export default function App() {
  const [focusedId, setFocusedId] = useState<string>('workspace')
  const { entities, refresh } = useEntities()
  const { messages, streaming, streamMsg, sendMessage } = useChat(focusedId, refresh)

  const focused = entities.find(e => e.id === focusedId) ?? null

  return (
    <div className="app">
      <Rail />
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
      <AdvisorRail focusedId={focusedId} />
    </div>
  )
}
