import './App.css'
import Rail from './components/Rail'
import ProjectTree from './components/ProjectTree'
import ChatPane from './components/ChatPane'
import AdvisorRail from './components/AdvisorRail'
import { useChat } from './useChat'

export default function App() {
  const { messages, streaming, streamMsg, sendMessage } = useChat()

  return (
    <div className="app">
      <Rail />
      <ProjectTree />
      <ChatPane
        messages={messages}
        streaming={streaming}
        streamMsg={streamMsg}
        onSend={sendMessage}
      />
      <AdvisorRail />
    </div>
  )
}
