/**
 * URL ↔ app-state bridge for Phase 1 routing.
 *
 * The URL is canonical for three things:
 *   - project id           → :pid    (path segment after /p/)
 *   - thread id            → :tid    (after /t/, optional; default = 'default')
 *   - focused entity id    → :eid    (after /e/, optional; default = 'workspace')
 *
 * Routes recognized:
 *   /                                        → home (no project)
 *   /p/<pid>                                 → project, no thread or focus
 *   /p/<pid>/e/<eid>                         → project + focus (no thread)
 *   /p/<pid>/t/<tid>                         → project + thread
 *   /p/<pid>/t/<tid>/e/<eid>                 → project + thread + focus
 *
 * Existing code uses setters like setFocusedId(x) and setThreadId(t) freely.
 * This hook exposes setFocus / setThread / setProject with the same shape;
 * they navigate() instead of calling useState setters, so the browser's
 * Back / Forward / reload / bookmarks all just work.
 */
import { useLocation, useNavigate } from 'react-router-dom'

export interface UrlState {
  /** Current project id, or null when at home (no /p/<pid>). */
  pid:        string | null
  /** Focused entity id. 'workspace' = no entity focused (the no-/e/ case). */
  focusedId:  string
  /** Current thread id. 'default' = the project's implicit default thread. */
  threadId:   string
  /** Are we in the home view (no project)? */
  isHome:     boolean
  /** Push the app to a new URL with the given pieces, preserving the others. */
  setFocus:   (eid: string) => void
  setThread:  (tid: string) => void
  setProject: (pid: string | null) => void
  /** Convenience: jump to home (the project picker). */
  goHome:     () => void
}

function buildPath(pid: string | null, tid: string, eid: string): string {
  if (!pid) return '/'
  const tSeg = tid && tid !== 'default' ? `/t/${encodeURIComponent(tid)}` : ''
  const eSeg = eid && eid !== 'workspace' ? `/e/${encodeURIComponent(eid)}` : ''
  return `/p/${encodeURIComponent(pid)}${tSeg}${eSeg}`
}

/** Parse the current pathname into (pid, tid, eid). */
function parse(pathname: string): { pid: string | null; tid: string; eid: string } {
  // Strip leading slash and split. Handle: /, /p/<pid>(/t/<tid>)?(/e/<eid>)?
  const parts = pathname.split('/').filter(Boolean)
  if (parts.length === 0 || parts[0] !== 'p' || !parts[1]) {
    return { pid: null, tid: 'default', eid: 'workspace' }
  }
  const pid = decodeURIComponent(parts[1])
  let tid = 'default'
  let eid = 'workspace'
  let i = 2
  while (i < parts.length) {
    if (parts[i] === 't' && parts[i + 1]) { tid = decodeURIComponent(parts[i + 1]); i += 2 }
    else if (parts[i] === 'e' && parts[i + 1]) { eid = decodeURIComponent(parts[i + 1]); i += 2 }
    else { i += 1 }   // unknown segment — skip
  }
  return { pid, tid, eid }
}

export function useUrlState(): UrlState {
  const location = useLocation()
  const navigate = useNavigate()
  const { pid, tid, eid } = parse(location.pathname)

  const setFocus = (newEid: string) => {
    if (!pid) return   // no project to focus into
    const path = buildPath(pid, tid, newEid)
    if (path !== location.pathname) navigate(path)
  }
  const setThread = (newTid: string) => {
    if (!pid) return
    // Switching threads typically clears focus to the thread's overview;
    // callers that want to keep focus can explicitly setFocus after.
    const path = buildPath(pid, newTid, 'workspace')
    if (path !== location.pathname) navigate(path)
  }
  const setProject = (newPid: string | null) => {
    const path = buildPath(newPid, 'default', 'workspace')
    if (path !== location.pathname) navigate(path)
  }
  const goHome = () => {
    if (location.pathname !== '/') navigate('/')
  }

  return {
    pid,
    focusedId: eid,
    threadId:  tid,
    isHome:    pid === null,
    setFocus, setThread, setProject, goHome,
  }
}
