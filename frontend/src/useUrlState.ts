/**
 * URL ↔ app-state bridge.
 *
 * The URL is canonical for things that change the *content* the user is
 * looking at:
 *   - project id           → /p/<pid>
 *   - thread id            → /t/<tid>      (default = the implicit default thread)
 *   - focused entity id    → /e/<eid>      (default = workspace, i.e. no focus)
 *   - scene                → /overview     (project-wide) or /inventory (per-thread)
 *   - open file            → /files/<path> (FilesView opens a file in the center)
 *
 * Rail tab (threads / claims / data / runs / results / files) is NOT a URL
 * state — it's a filter on the rail list, owned by App.tsx as React state.
 * This avoids cluttering history with cosmetic filter changes; every Back
 * is a real content change.
 *
 * Legacy URLs that included a section segment (e.g. /p/X/runs/e/Y) are
 * still parsed — the section name is consumed and dropped, so bookmarks
 * from Phase 2 continue to land on the right entity.
 *
 * Routes recognized (canonical):
 *   /                                       → home
 *   /p/<pid>                                → project
 *   /p/<pid>/t/<tid>                        → + thread
 *   /p/<pid>/t/<tid>/e/<eid>                → + thread + entity
 *   /p/<pid>/t/<tid>/inventory              → scene: thread inventory
 *   /p/<pid>/e/<eid>                        → + entity (no thread)
 *   /p/<pid>/files/<file-path>              → file open in center
 *   /p/<pid>/overview                       → scene: project overview
 *
 * Legacy (tolerated, parsed identically minus the section name):
 *   /p/<pid>/<section>[/t/<tid>][/e/<eid>]  → section dropped
 */
import { useLocation, useNavigate } from 'react-router-dom'

export type Scene = 'overview' | 'inventory' | null

const KNOWN_SECTIONS = new Set(['threads', 'claims', 'data', 'runs', 'results', 'files'])

export interface UrlState {
  pid:           string | null
  threadId:      string              // 'default' = implicit default thread
  focusedId:     string              // 'workspace' = no entity focused
  scene:         Scene
  /** Path of the file currently open in the center (FilesView opens a file)
   *  or '' if no file is open. */
  filePath:      string
  isHome:        boolean

  setFocus:           (eid: string) => void
  setThread:          (tid: string) => void
  setProject:         (pid: string | null) => void
  setFilePath:        (path: string) => void
  setScene:           (scene: Scene) => void
  /** Combined: thread + focus in one navigation (single history entry). */
  setThreadAndFocus:  (tid: string, eid: string) => void
  /** Combined: any subset of URL pieces in one navigation. */
  setNav:             (next: Partial<Pick<UrlState, 'threadId' | 'focusedId' | 'scene' | 'filePath'>>) => void
  goHome:             () => void
}

interface Parsed {
  pid:      string | null
  tid:      string
  eid:      string
  scene:    Scene
  filePath: string
}

function parse(pathname: string): Parsed {
  const parts = pathname.split('/').filter(Boolean)
  if (parts.length === 0 || parts[0] !== 'p' || !parts[1]) {
    return { pid: null, tid: 'default', eid: 'workspace', scene: null, filePath: '' }
  }
  const pid = decodeURIComponent(parts[1])
  let i = 2
  let scene: Scene = null
  let filePath = ''
  let tid = 'default'
  let eid = 'workspace'

  // Tolerate a section segment right after pid. 'files' is special — it
  // captures the rest of the URL as the file path. Other section names
  // (threads/claims/runs/...) are consumed and dropped (legacy Phase-2 URLs).
  if (i < parts.length && KNOWN_SECTIONS.has(parts[i])) {
    const seg = parts[i]
    i += 1
    if (seg === 'files' && i < parts.length) {
      filePath = parts.slice(i).map(decodeURIComponent).join('/')
      return { pid, tid, eid, scene, filePath }
    }
  }

  // Project-overview scene — mutually exclusive with everything else.
  if (i < parts.length && parts[i] === 'overview') {
    return { pid, tid, eid, scene: 'overview', filePath: '' }
  }

  // /t/<tid> [ /inventory ] [ /e/<eid> ] in any order; scan tolerantly.
  while (i < parts.length) {
    const seg = parts[i]
    if (seg === 't' && parts[i + 1]) { tid = decodeURIComponent(parts[i + 1]); i += 2 }
    else if (seg === 'e' && parts[i + 1]) { eid = decodeURIComponent(parts[i + 1]); i += 2 }
    else if (seg === 'inventory') { scene = 'inventory'; i += 1 }
    else { i += 1 }
  }
  return { pid, tid, eid, scene, filePath }
}

function buildPath(p: Parsed): string {
  if (!p.pid) return '/'
  // A file-open URL is its own shape (no t/e segments are meaningful).
  if (p.filePath) {
    return `/p/${encodeURIComponent(p.pid)}/files/${p.filePath.split('/').map(encodeURIComponent).join('/')}`
  }
  // Project overview replaces thread + entity.
  if (p.scene === 'overview') {
    return `/p/${encodeURIComponent(p.pid)}/overview`
  }
  const segs: string[] = ['p', encodeURIComponent(p.pid)]
  if (p.tid && p.tid !== 'default') segs.push('t', encodeURIComponent(p.tid))
  if (p.eid && p.eid !== 'workspace') segs.push('e', encodeURIComponent(p.eid))
  if (p.scene === 'inventory') segs.push('inventory')
  return '/' + segs.join('/')
}

export function useUrlState(): UrlState {
  const location = useLocation()
  const navigate = useNavigate()
  const parsed = parse(location.pathname)

  const go = (next: Parsed) => {
    const path = buildPath(next)
    if (path !== location.pathname) navigate(path)
  }

  // Focus / thread changes implicitly drop any active scene — opening an
  // entity or switching threads is a scene transition.
  const setFocus           = (eid: string) => parsed.pid && go({ ...parsed, eid, scene: null })
  const setThread          = (tid: string) => parsed.pid && go({ ...parsed, tid, eid: 'workspace', scene: null })
  const setProject         = (pid: string | null) => go({
    pid, tid: 'default', eid: 'workspace', scene: null, filePath: '',
  })
  const setFilePath        = (path: string) => parsed.pid && go({
    ...parsed, filePath: path, tid: 'default', eid: 'workspace', scene: null,
  })
  const setScene           = (scene: Scene) => parsed.pid && go({ ...parsed, scene })
  const setThreadAndFocus  = (tid: string, eid: string) => parsed.pid && go({ ...parsed, tid, eid, scene: null })
  const setNav             = (next: Partial<Pick<UrlState, 'threadId' | 'focusedId' | 'scene' | 'filePath'>>) => {
    if (!parsed.pid) return
    go({
      ...parsed,
      tid:      next.threadId ?? parsed.tid,
      eid:      next.focusedId ?? parsed.eid,
      scene:    next.scene    !== undefined ? next.scene    : parsed.scene,
      filePath: next.filePath !== undefined ? next.filePath : parsed.filePath,
    })
  }
  const goHome             = () => { if (location.pathname !== '/') navigate('/') }

  return {
    pid:       parsed.pid,
    threadId:  parsed.tid,
    focusedId: parsed.eid,
    scene:     parsed.scene,
    filePath:  parsed.filePath,
    isHome:    parsed.pid === null,
    setFocus, setThread, setProject, setFilePath, setScene,
    setThreadAndFocus, setNav, goHome,
  }
}
