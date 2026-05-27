/**
 * URL ↔ app-state bridge.
 *
 * The URL is canonical for: project, rail section (tab), thread, focused
 * entity, scene, and open-file path. Setters preserve other pieces.
 *
 * # Tab → item coalesce
 *
 * Tab clicks push a history entry. But clicking an item RIGHT AFTER a
 * tab click — within COALESCE_WINDOW_MS — REPLACES that tab entry, so the
 * combined "I flipped to Runs and clicked a run" is a single Back step
 * back to where the user started exploring, not two.
 *
 * Without this rule, Back from a focused item leaves the rail stranded
 * on the tab the user just visited, which the user perceives as
 * inconsistent: the center reverted but the rail didn't.
 *
 * Other navigations (thread switch, file open, scene change) reset the
 * flag — only an item-focus immediately following a tab click coalesces.
 *
 * # Grammar
 *
 *   /                                                       home
 *   /p/<pid>                                                project (default section = threads)
 *   /p/<pid>/<section>                                      section explicit
 *   /p/<pid>/<section>/t/<tid>                              + thread
 *   /p/<pid>/<section>/t/<tid>/e/<eid>                      + thread + entity
 *   /p/<pid>/<section>/t/<tid>/inventory                    scene: thread inventory
 *   /p/<pid>/<section>/e/<eid>                              + entity (no thread)
 *   /p/<pid>/files/<file-path>                              file open in center
 *   /p/<pid>/overview                                       scene: project overview
 *   /p/<pid>/t/<tid>...                                     legacy: section = threads
 */
import { useLocation, useNavigate } from 'react-router-dom'

export type Section = 'threads' | 'claims' | 'data' | 'runs' | 'results' | 'files'
export type Scene   = 'overview' | 'inventory' | null

const SECTIONS: readonly Section[] = ['threads', 'claims', 'data', 'runs', 'results', 'files']
const DEFAULT_SECTION: Section = 'threads'
const COALESCE_WINDOW_MS = 1500

/* Module-level flag: was the most recent navigation a tab click that's
 * still eligible for coalescing? Set by setSection, cleared by other
 * setters and on timeout. The flag is intentionally not React state —
 * it's a transient signal between two synchronous click handlers, not
 * something the UI ever reads. */
let _lastWasTabChange = false
let _coalesceTimer: ReturnType<typeof setTimeout> | null = null
function markTabChange() {
  _lastWasTabChange = true
  if (_coalesceTimer) clearTimeout(_coalesceTimer)
  _coalesceTimer = setTimeout(() => { _lastWasTabChange = false }, COALESCE_WINDOW_MS)
}
function consumeCoalesce(): boolean {
  const v = _lastWasTabChange
  _lastWasTabChange = false
  if (_coalesceTimer) { clearTimeout(_coalesceTimer); _coalesceTimer = null }
  return v
}
function clearCoalesce() {
  _lastWasTabChange = false
  if (_coalesceTimer) { clearTimeout(_coalesceTimer); _coalesceTimer = null }
}

export interface UrlState {
  pid:          string | null
  section:      Section
  threadId:     string
  focusedId:    string
  scene:        Scene
  filePath:     string
  isHome:       boolean

  setFocus:           (eid: string) => void
  setThread:          (tid: string) => void
  setProject:         (pid: string | null) => void
  setSection:         (section: Section) => void
  setFilePath:        (path: string) => void
  setScene:           (scene: Scene) => void
  setThreadAndFocus:  (tid: string, eid: string) => void
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

  if (i < parts.length && isSection(parts[i])) {
    section = parts[i] as Section
    i += 1
    if (section === 'files' && i < parts.length) {
      filePath = parts.slice(i).map(decodeURIComponent).join('/')
      return { pid, section, tid, eid, scene, filePath }
    }
  }

  if (i < parts.length && parts[i] === 'overview') {
    return { pid, section, tid, eid, scene: 'overview', filePath: '' }
  }

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
  if (p.section === 'files' && p.filePath) {
    return `/p/${encodeURIComponent(p.pid)}/files/${p.filePath.split('/').map(encodeURIComponent).join('/')}`
  }
  if (p.scene === 'overview') {
    return `/p/${encodeURIComponent(p.pid)}/overview`
  }
  const segs: string[] = ['p', encodeURIComponent(p.pid)]
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

  const go = (next: Parsed, opts?: { replace?: boolean }) => {
    const path = buildPath(next)
    if (path !== location.pathname) navigate(path, { replace: !!opts?.replace })
  }

  // setSection: tab click. Pushes, but marks the change as eligible for
  // coalesce — if setFocus fires within COALESCE_WINDOW_MS, that focus
  // will REPLACE this entry instead of pushing.
  const setSection = (section: Section) => {
    if (!parsed.pid) return
    markTabChange()
    go({ ...parsed, section, filePath: '', scene: null })
  }

  // setFocus: item click. Replaces if the previous navigation was a tab
  // click within the coalesce window. Otherwise pushes normally.
  const setFocus = (eid: string) => {
    if (!parsed.pid) return
    const coalesce = consumeCoalesce()
    go({ ...parsed, eid, scene: null }, { replace: coalesce })
  }

  // Everything else clears the flag — only tab→item coalesces.
  const setThread = (tid: string) => {
    if (!parsed.pid) return
    clearCoalesce()
    go({ ...parsed, tid, eid: 'workspace', scene: null })
  }
  const setProject = (pid: string | null) => {
    clearCoalesce()
    go({ pid, section: DEFAULT_SECTION, tid: 'default', eid: 'workspace', scene: null, filePath: '' })
  }
  const setFilePath = (path: string) => {
    if (!parsed.pid) return
    clearCoalesce()
    go({ ...parsed, section: 'files', filePath: path, tid: 'default', eid: 'workspace', scene: null })
  }
  const setScene = (scene: Scene) => {
    if (!parsed.pid) return
    clearCoalesce()
    go({ ...parsed, scene })
  }
  const setThreadAndFocus = (tid: string, eid: string) => {
    if (!parsed.pid) return
    clearCoalesce()
    go({ ...parsed, tid, eid, scene: null })
  }
  const setNav = (next: Partial<Pick<UrlState, 'section' | 'threadId' | 'focusedId' | 'scene' | 'filePath'>>) => {
    if (!parsed.pid) return
    clearCoalesce()
    go({
      ...parsed,
      section:  next.section  ?? parsed.section,
      tid:      next.threadId ?? parsed.tid,
      eid:      next.focusedId ?? parsed.eid,
      scene:    next.scene    !== undefined ? next.scene    : parsed.scene,
      filePath: next.filePath !== undefined ? next.filePath : parsed.filePath,
    })
  }
  const goHome = () => {
    clearCoalesce()
    if (location.pathname !== '/') navigate('/')
  }

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

// Exposed for tests only — let tests reset the coalesce state between cases.
export const _resetCoalesceForTesting = clearCoalesce
