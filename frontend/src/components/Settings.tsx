/**
 * Settings — a tabbed panel.
 *   • Agent       — provider (Anthropic / OpenAI), model, and credentials.
 *   • Environment — what this workspace can run + the pipeline-suggestion gate.
 * (More tabs can slot in later.) Each tab owns its own data loading.
 */
import { useState } from 'react'
import './Settings.css'
import AgentTab from './AgentTab'
import EnvironmentTab from './EnvironmentTab'

interface Props { onClose: () => void }

type TabId = 'agent' | 'environment'
const TABS: { id: TabId; label: string }[] = [
  { id: 'agent', label: 'Agent' },
  { id: 'environment', label: 'Environment' },
]

export default function Settings({ onClose }: Props) {
  const [tab, setTab] = useState<TabId>('agent')

  return (
    <div className="settings-backdrop" onClick={onClose}>
      <div className="settings" onClick={e => e.stopPropagation()}>
        <div className="settings__head">
          <h2>Settings</h2>
          <button onClick={onClose} className="settings__close" title="Close">×</button>
        </div>

        <div className="settings__tabs" role="tablist" aria-label="Settings sections">
          {TABS.map(t => (
            <button key={t.id} role="tab" aria-selected={tab === t.id}
              className={`settings__tab ${tab === t.id ? 'is-active' : ''}`}
              onClick={() => setTab(t.id)}>{t.label}</button>
          ))}
        </div>

        <div className="settings__body">
          {tab === 'agent' ? <AgentTab /> : <EnvironmentTab />}
        </div>
      </div>
    </div>
  )
}
