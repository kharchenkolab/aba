/**
 * Platform-imports lint test.
 *
 * Wave 1 Track C established src/platform/ as the no-bio shell layer.
 * The invariant: no file under src/platform/ may import from
 * src/bio/. Bio components are registered into shared lib/ registries
 * at startup (App.tsx → import './bio'); the platform reads those
 * registries by name and never references a bio module directly.
 *
 * P3.4b (modularity_audit2): also ratchet bio TYPE LITERALS in
 * platform/ + components/ — the components/ shell bucket was unguarded
 * and the import check missed string literals (entity.type === 'figure').
 * Existing files are grandfathered (cleaned in 3.4c's per-type focus-view
 * contract); NEW files must not add bio type literals.
 *
 * Mirrors the backend's tests/check_platform_purity.py + check_seam.sh.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'fs'
import { dirname, join } from 'path'
import { fileURLToPath } from 'url'

const PLATFORM_DIR = dirname(fileURLToPath(import.meta.url))
const COMPONENTS_DIR = join(PLATFORM_DIR, '..', 'components')

// Bio entity-type names — string literals of these in the platform/components
// shell mean domain coupling (the shell should read the registry/contract).
const BIO_TYPES = ['figure', 'table', 'cell', 'plan', 'dataset', 'analysis',
  'result', 'finding', 'claim', 'narrative', 'thread']

// Files grandfathered with bio type literals as of P3.4b. Burn down in 3.4c
// (per-type chrome → the registry-projected focus-view contract). NEW files
// must NOT be added here — add the contract instead.
const LITERAL_BASELINE = new Set([
  'components/FocusCanvas.tsx',
  'components/highlightTools.ts',
  'components/icons.tsx',
  'components/ResultList.tsx',
  'components/TracePanel.tsx',
  'platform/ChatPane.tsx',
])

// Files with inline `/api/` fetch as of P3.4a. Burn down into lib/api.ts typed
// helpers. NEW platform/components code must route through lib/api.ts, not here.
const FETCH_BASELINE = new Set([
  'components/AdvisorStrip.tsx',
  'components/FocusCanvas.tsx',
  'components/Proposals.tsx',
  'components/SearchModal.tsx',
  'components/Settings.tsx',
  'components/SpecPicker.tsx',
  'components/ThreadHeader.tsx',
  'platform/Drawer.tsx',
  'platform/Rail.tsx',
  'platform/UploadDrop.tsx',
])


function _allTsFilesUnder(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) {
      out.push(..._allTsFilesUnder(p))
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(p)
    }
  }
  return out
}


function _srcRel(f: string): string {
  const i = f.indexOf('/src/')
  return i >= 0 ? f.slice(i + 5) : f
}


describe('platform purity', () => {
  it('no src/platform/ file imports from src/bio/', () => {
    const files = _allTsFilesUnder(PLATFORM_DIR)
    expect(files.length).toBeGreaterThan(0)   // sanity: tree wasn't empty
    const violations: string[] = []
    const re = /from\s+['"][^'"]*\bbio\b[^'"]*['"]/g
    for (const f of files) {
      const src = readFileSync(f, 'utf-8')
      const m = src.match(re)
      if (m) {
        violations.push(`${f}: ${m.join(', ')}`)
      }
    }
    if (violations.length) {
      throw new Error(
        'src/platform/ files must not import from src/bio/:\n' +
        violations.map(v => '  ' + v).join('\n') + '\n\n' +
        'Bio components are registered into lib/ registries at startup; ' +
        'platform reads via lib/ lookups, never via direct bio imports.'
      )
    }
  })

  it('no NEW bio type literals in platform/ or components/ (ratchet)', () => {
    const files = [..._allTsFilesUnder(PLATFORM_DIR), ..._allTsFilesUnder(COMPONENTS_DIR)]
    const litRe = new RegExp(`['"](${BIO_TYPES.join('|')})['"]`)
    const violations: string[] = []
    for (const f of files) {
      const rel = _srcRel(f)
      if (LITERAL_BASELINE.has(rel)) continue   // grandfathered (3.4c burns these down)
      if (litRe.test(readFileSync(f, 'utf-8'))) {
        violations.push(rel)
      }
    }
    if (violations.length) {
      throw new Error(
        'bio type literals in non-baseline platform/components files:\n' +
        violations.map(v => '  ' + v).join('\n') + '\n\n' +
        'The shell must read the registry / focus-view contract, not name bio ' +
        'types. (Grandfathered files are in LITERAL_BASELINE; do not add new ones.)'
      )
    }
  })

  it('no NEW inline /api/ fetch in platform/ or components/ — use lib/api.ts (ratchet)', () => {
    const files = [..._allTsFilesUnder(PLATFORM_DIR), ..._allTsFilesUnder(COMPONENTS_DIR)]
    const violations: string[] = []
    for (const f of files) {
      const rel = _srcRel(f)
      if (FETCH_BASELINE.has(rel)) continue
      const src = readFileSync(f, 'utf-8')
      if (/\bfetch\s*\(/.test(src) && src.includes('/api/')) {
        violations.push(rel)
      }
    }
    if (violations.length) {
      throw new Error(
        'inline /api/ fetch in non-baseline platform/components files:\n' +
        violations.map(v => '  ' + v).join('\n') + '\n\n' +
        'Route backend calls through src/lib/api.ts (apiGet/apiPost/... or a typed ' +
        'helper), not inline fetch. (Grandfathered files are in FETCH_BASELINE.)'
      )
    }
  })
})
