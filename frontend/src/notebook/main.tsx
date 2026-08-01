import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import Record from './Record'
import { fetchLiveWorld, triageApi } from './live'
import type { World } from './world'
import './record.css'

/** `/notebook.html` renders the fixture, exactly as before.
 *  `/notebook.html?live=1[&api=http://localhost:8000][&project=<pid>]`
 *  renders the SAME renderer over a real project's World (phase-1 face).
 *  `&triage=1` additionally wires accept/dismiss/undo to the shared
 *  proposal endpoints — only meaningful against a FULL backend (the
 *  read-only sidecar does not mount them). */
function Live(props: { api: string; project?: string; triage?: boolean }) {
  const [world, setWorld] = useState<World | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    fetchLiveWorld(props.api, props.project)
      .then(setWorld)
      .catch(e => setErr(String(e)))
  }, [props.api, props.project])
  if (err) {
    return (
      <div style={{ padding: '2rem', fontFamily: 'monospace' }}>
        <p>live world unavailable — {err}</p>
        <p>is the backend up, and CORS open for this origin?
          {' '}<a href="/notebook.html">fixture face</a></p>
      </div>
    )
  }
  if (!world) return <div style={{ padding: '2rem' }}>assembling the record…</div>
  return <Record world={world}
                 triage={props.triage
                   ? triageApi(props.api, props.project) : undefined} />
}

const params = new URLSearchParams(window.location.search)
const root = createRoot(document.getElementById('root')!)
root.render(
  <StrictMode>
    {params.get('live')
      ? <Live api={params.get('api') || 'http://localhost:8000'}
              project={params.get('project') || undefined}
              triage={params.get('triage') === '1'} />
      : <Record />}
  </StrictMode>,
)
