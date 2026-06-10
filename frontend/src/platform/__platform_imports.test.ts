/**
 * Platform-imports lint test.
 *
 * Wave 1 Track C established src/platform/ as the no-bio shell layer.
 * The invariant: no file under src/platform/ may import from
 * src/bio/. Bio components are registered into shared lib/ registries
 * at startup (App.tsx → import './bio'); the platform reads those
 * registries by name and never references a bio module directly.
 *
 * Mirrors the backend's tests/check_platform_purity.py + the
 * pytest-discovered tests/test_platform_test_imports.py.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'fs'
import { dirname, join } from 'path'
import { fileURLToPath } from 'url'

// Resolve relative to the .ts file's path on disk. Plain
// `new URL('.', import.meta.url).pathname` returns a vite-mapped
// virtual path; fileURLToPath translates back to the real fs path.
const PLATFORM_DIR = dirname(fileURLToPath(import.meta.url))


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


describe('platform purity', () => {
  it('no src/platform/ file imports from src/bio/', () => {
    const files = _allTsFilesUnder(PLATFORM_DIR)
    expect(files.length).toBeGreaterThan(0)   // sanity: tree wasn't empty
    const violations: string[] = []
    // Match both `from '../bio/...'` and `from 'src/bio/...'` and `from './bio/...'`
    // — sample any path-segment whose components include `bio`.
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
})
