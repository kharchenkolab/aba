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
  const [projects, setProjects] = useState<
    { id: string; name?: string; last_touched?: string }[] | null>(null)
  const load = () => {
    fetchLiveWorld(props.api, props.project)
      .then(setWorld)
      .catch(e => {
        setErr(String(e))
        // no project bound (fresh server, bare URL) — the face offers the
        // project list instead of an error: an entrance, not a wall
        if (!props.project) {
          fetch(`${props.api}/api/projects`)
            .then(r => r.ok ? r.json() : [])
            .then((rows: { id: string; name?: string; last_touched?: string }[]) =>
              setProjects([...rows].sort((a, b) =>
                (b.last_touched || '').localeCompare(a.last_touched || ''))))
            .catch(() => {})
        }
      })
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [props.api, props.project])
  if (err) {
    if (projects?.length) {
      return (
        <div style={{ padding: '2.5rem', fontFamily: 'Georgia, serif', maxWidth: 560 }}>
          <h2 style={{ marginTop: 0 }}>The Record</h2>
          <p style={{ color: '#666' }}>which project shall we open?</p>
          {projects.map(p => (
            <p key={p.id} style={{ margin: '0.4rem 0' }}>
              <a href={`/notebook.html?live=1&project=${p.id}`}
                 style={{ color: '#1a7f74' }}>
                {p.name || p.id}
              </a>
              {p.last_touched &&
                <span style={{ color: '#999', fontSize: '0.85em' }}> · last touched {p.last_touched.slice(0, 10)}</span>}
            </p>
          ))}
        </div>
      )
    }
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
                 onRefresh={load}
                 triage={props.triage
                   ? triageApi(props.api, props.project) : undefined} />
}

const params = new URLSearchParams(window.location.search)
const root = createRoot(document.getElementById('root')!)
root.render(
  <StrictMode>
    {params.get('live')
      /* api defaults to SAME ORIGIN (the serving backend) — never to a
         hardcoded host; cross-origin use passes ?api= explicitly */
      ? <Live api={params.get('api') || ''}
              project={params.get('project') || undefined}
              triage={params.get('triage') !== '0'} />
      : <Record />}
  </StrictMode>,
)
