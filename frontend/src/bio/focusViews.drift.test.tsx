/**
 * §5 drift banner + verified relink (more_weft_ui.md): the banner renders
 * ONLY on an entity carrying a recorded drift/missing flag — never ambient —
 * and relink is VERIFIED: a content mismatch (409) demotes to the
 * new-version flow instead of silently rebinding the registration.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { DriftBanner } from './focusViews'
import type { Entity } from '../types'

function entity(metadata: Record<string, unknown>): Entity {
  return {
    id: 'ds_drift1', type: 'dataset', title: 'shared table', status: 'active',
    artifact_path: null, producing_params: null, parent_entity_id: null,
    scenario_of: null, metadata, tags: [], notes: null, pinned: false,
    exec_id: null, artifact_kind: null, artifact_idx: null,
    derivation: null, actor: null,
  } as unknown as Entity
}

function mockFetch(status: number) {
  globalThis.fetch = vi.fn().mockImplementation(() =>
    Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve({}) }),
  ) as unknown as typeof globalThis.fetch
}

describe('DriftBanner', () => {
  let origFetch: typeof globalThis.fetch
  beforeEach(() => { origFetch = globalThis.fetch })
  afterEach(() => { globalThis.fetch = origFetch; vi.restoreAllMocks() })

  it('renders NOTHING without a recorded drift flag (absence is the default)', () => {
    const { container } = render(
      <DriftBanner entity={entity({ home: { site: 'siteC', path: '/data/x' } })}
                   onChange={() => {}} />,
    )
    expect(container.innerHTML).toBe('')
  })

  it('changed source: names the home, offers new-version prefill + relink + re-check', () => {
    const prefills: string[] = []
    render(
      <DriftBanner
        entity={entity({ home: { site: 'siteC', path: '/data/x' }, source_changed: true })}
        onChange={() => {}} onPrefill={t => prefills.push(t)} />,
    )
    expect(screen.getByText(/\/data\/x on siteC has changed since registration/)).toBeTruthy()
    fireEvent.click(screen.getByText('Use as new version'))
    expect(prefills[0]).toContain('NEW VERSION')
    expect(prefills[0]).toContain('ds_drift1')
    expect(screen.getByText('It moved — relink…')).toBeTruthy()
    expect(screen.getByText('Re-check')).toBeTruthy()
  })

  it('missing source: says gone/unreachable and does NOT offer new-version', () => {
    render(
      <DriftBanner
        entity={entity({ home: { site: 'siteC', path: '/data/x' }, source_missing: true })}
        onChange={() => {}} onPrefill={() => {}} />,
    )
    expect(screen.getByText(/is gone or unreachable/)).toBeTruthy()
    expect(screen.queryByText('Use as new version')).toBeNull()
  })

  it('relink verifies content: a 409 mismatch demotes to the new-version flow', async () => {
    mockFetch(409)
    render(
      <DriftBanner
        entity={entity({ home: { path: '/data/x' }, source_changed: true })}
        onChange={() => {}} />,
    )
    fireEvent.click(screen.getByText('It moved — relink…'))
    fireEvent.change(screen.getByPlaceholderText('new path of the same data…'),
                     { target: { value: '/data/moved' } })
    await act(async () => { fireEvent.click(screen.getByText('Verify & relink')) })
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/datasets/ds_drift1/relink', expect.objectContaining({ method: 'POST' }))
    expect(screen.getByText(/differs — use .new version. instead/)).toBeTruthy()
  })

  it('relink accepts on content match', async () => {
    mockFetch(200)
    render(
      <DriftBanner
        entity={entity({ home: { path: '/data/x' }, source_changed: true })}
        onChange={() => {}} />,
    )
    fireEvent.click(screen.getByText('It moved — relink…'))
    fireEvent.change(screen.getByPlaceholderText('new path of the same data…'),
                     { target: { value: '/data/moved' } })
    await act(async () => { fireEvent.click(screen.getByText('Verify & relink')) })
    expect(screen.getByText(/relinked — same content, new home/)).toBeTruthy()
  })
})
