/**
 * URL ↔ app-state bridge (Phase 1 + Phase 2).
 *
 * URL grammar:
 *
 *   /                                                home (project picker)
 *   /p/<pid>                                         project, default section, no t/e
 *   /p/<pid>/<section>                               section explicit
 *   /p/<pid>/<section>/t/<tid>                       + thread
 *   /p/<pid>/<section>/t/<tid>/e/<eid>               + thread + entity
 *   /p/<pid>/<section>/t/<tid>/inventory             scene: thread inventory
 *   /p/<pid>/<section>/e/<eid>                       + entity (no thread)
 *   /p/<pid>/files/<file-path>                       files + a file open in the center
 *   /p/<pid>/overview                                scene: project overview (mutually exclusive
 *                                                    with section + thread + entity)
 *   /p/<pid>/t/<tid>...                              legacy: section defaults to 'threads'
 *   /p/<pid>/e/<eid>                                 legacy
 *
 * SECTIONS = rail tabs. SCENES = full-canvas modes that replace the center
 * column (overview is project-wide; inventory is thread-scoped).
 *
 * All setters are immutable URL updates: they preserve the other pieces of
 * the URL, so swapping a section keeps your focus + thread, etc. Combined
 * navigation (`setThreadAndFocus`) avoids two history entries when both
 * change atomically.
 */
import { useLocation, useNavigate } from 'react-router-dom'

export type Section = 'threads' | 'claims' | 'data' | 'runs' | 'results' | 'files'
export type Scene   = 'overview' | 'inventory' | null

const SECTIONS: readonly Section[] = ['threads', 'claims', 'data', 'runs', 'results', 'files'] as const
const DEFAULT_SECTION: Section = 'threads'

export interface UrlState {
  pid:           string | null
  section:       Section            // always present; defaults to 'threads'
  threadId:      string             // 'default' = the project's implicit default thread
  focusedId:     string             // 'workspace' = no entity focused
  scene:         Scene              // 'overview' or 'inventory' if active; else null
  /** When section==='files', the path of the file currently open in the center
   *  ('' = the tree itself, no file open). */
  filePath:      string
  isHome:        boolean

  // Setters (each preserves the other URL pieces).
  setFocus:           (eid: string) => void
  setThread:          (tid: string) => void
  setProject:         (pid: string | null) => void
  setSection:         (section: Section) => void
  setFilePath:        (path: string) => void          // also implies section='files'
  setScene:           (scene: Scene) => void
  /** Combined: thread + focus in one navigation (single history entry). */
  setThreadAndFocus:  (tid: string, eid: string) => void
  /** Combined: any subset of the URL pieces in one navigation. Use this for
   *  transitions that change two or more pieces at once (e.g. "switch to
   *  thread Y and enter inventory") — avoids two history entries. */
  setNav:             (next: Partial<Pick<UrlState, 'section' | 'threadId' | 'focusedId' | 'scene' | 'filePath'>>) => void
  goHome:             () => void
}

interface Parsed {
  pid:      string | null
  section:  Section
  tid:      string
  eid:      string
  scene:    Scene
  filePath: string
}

function isSection(s: string): s is Section {
  return (SECTIONS as readonly string[]).includes(s)
}

function parse(pathname: string): Parsed {
  const parts = pathname.split('/').filter(Boolean)
  if (parts.length === 0 || parts[0] !== 'p' || !parts[1]) {
    return { pid: null, section: DEFAULT_SECTION, tid: 'default', eid: 'workspace', scene: null, filePath: '' }
  }
  const pid = decodeURIComponent(parts[1])
  let i = 2
  let section: Section = DEFAULT_SECTION
  let scene: Scene = null
  let filePath = ''
  let tid = 'default'
  let eid = 'workspace'

  // Optional section right after pid.
  if (i < parts.length && isSection(parts[i])) {
    section = parts[i] as Section
    i += 1
    if (section === 'files' && i < parts.length) {
      // Files: the rest of the URL is the file path. Stop further parsing.
      filePath = parts.slice(i).map(decodeURIComponent).join('/')
      return { pid, section, tid, eid, scene, filePath }
    }
  }

  // Project-overview scene: mutually exclusive with the rest.
  if (i < parts.length && parts[i] === 'overview') {
    return { pid, section, tid, eid, scene: 'overview', filePath: '' }
  }

  // /t/<tid> [ /inventory ] [ /e/<eid> ]   (in any order, scan tolerantly)
  while (i < parts.length) {
    const seg = parts[i]
    if (seg === 't' && parts[i + 1]) { tid = decodeURIComponent(parts[i + 1]); i += 2 }
    else if (seg === 'e' && parts[i + 1]) { eid = decodeURIComponent(parts[i + 1]); i += 2 }
    else if (seg === 'inventory') { scene = 'inventory'; i += 1 }
    else { i += 1 }
  }
  return { pid, section, tid, eid, scene, filePath }
}

function buildPath(p: Parsed): string {
  if (!p.pid) return '/'

  // Files + path is its own shape (no t/e segments are meaningful here).
  if (p.section === 'files' && p.filePath) {
    return `/p/${encodeURIComponent(p.pid)}/files/${p.filePath.split('/').map(encodeURIComponent).join('/')}`
  }
  // Project overview replaces section/thread/entity.
  if (p.scene === 'overview') {
    return `/p/${encodeURIComponent(p.pid)}/overview`
  }

  const segs: string[] = ['p', encodeURIComponent(p.pid)]
  // Omit section when default; explicit otherwise (so /p/X means "default").
  if (p.section !== DEFAULT_SECTION) segs.push(p.section)
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
  // entity or switching threads is a scene transition; the old scene
  // (overview / inventory) no longer applies. Without this, callers
  // would have to dispatch two navigations (clear scene, then focus),
  // doubling history entries and flashing the intermediate state.
  const setFocus           = (eid: string) => parsed.pid && go({ ...parsed, eid, scene: null })
  const setThread          = (tid: string) => parsed.pid && go({ ...parsed, tid, eid: 'workspace', scene: null })
  const setProject         = (pid: string | null) => go({
    pid, section: DEFAULT_SECTION, tid: 'default', eid: 'workspace', scene: null, filePath: '',
  })
  const setSection         = (section: Section) => parsed.pid && go({ ...parsed, section, filePath: '', scene: null })
  const setFilePath        = (path: string) => parsed.pid && go({
    ...parsed, section: 'files', filePath: path, tid: 'default', eid: 'workspace', scene: null,
  })
  const setScene           = (scene: Scene) => parsed.pid && go({ ...parsed, scene })
  const setThreadAndFocus  = (tid: string, eid: string) => parsed.pid && go({ ...parsed, tid, eid, scene: null })
  const setNav             = (next: Partial<Pick<UrlState, 'section' | 'threadId' | 'focusedId' | 'scene' | 'filePath'>>) => {
    if (!parsed.pid) return
    go({
      ...parsed,
      section:  next.section  ?? parsed.section,
      tid:      next.threadId ?? parsed.tid,
      eid:      next.focusedId ?? parsed.eid,
      scene:    next.scene    !== undefined ? next.scene    : parsed.scene,
      filePath: next.filePath !== undefined ? next.filePath : parsed.filePath,
    })
  }
  const goHome             = () => { if (location.pathname !== '/') navigate('/') }

  return {
    pid:       parsed.pid,
    section:   parsed.section,
    threadId:  parsed.tid,
    focusedId: parsed.eid,
    scene:     parsed.scene,
    filePath:  parsed.filePath,
    isHome:    parsed.pid === null,
    setFocus, setThread, setProject, setSection, setFilePath, setScene,
    setThreadAndFocus, setNav, goHome,
  }
}
