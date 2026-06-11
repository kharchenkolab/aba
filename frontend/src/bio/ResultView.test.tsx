/**
 * Integration tests for the redesigned ResultView.MemberPanel — the
 * focused-Result destination for a pinned figure with a revision chain.
 *
 * What we verify (the redesign's contract):
 *   1. The displayed <img> defaults to the LATEST revision in the chain,
 *      even though the member.ref points to the anchor (oldest).
 *   2. Clicking the ‹ chevron BELOW the figure swaps the <img> src to the
 *      previous revision in-place (no navigation away from the Result).
 *   3. SplitButton "Make a revision" from the latest fires action='revision'
 *      directly — no confirmation dialog.
 *   4. SplitButton "Make a revision…" from a non-latest opens the
 *      confirmation dialog explaining supersession; Confirm fires
 *      action='revision-supersede' carrying the displayed entity's id.
 *
 * The vitest+happy-dom harness mocks fetch — we intercept the two
 * endpoints the page hits: GET /api/entities/{id}/revisions (the
 * useFigureHistory hook) and any unrelated POST/PATCH (no-op).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import ResultView from './ResultView'
import type { Entity } from '../types'

const fig = (id: string, ts: string, title?: string): Entity => ({
  id,
  type: 'figure',
  title: title ?? `Figure ${id}`,
  status: 'active',
  artifact_path: `/artifacts/${id}.png`,
  created_at: ts,
  updated_at: ts,
} as unknown as Entity)


function makeResult(memberRef: string): Entity {
  return {
    id: 'res_1',
    type: 'result',
    title: 'Test result',
    status: 'active',
    artifact_path: null,
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
    metadata: {
      thread_id: 'thr_t',
      members: [{ id: 'm_1', kind: 'figure', ref: memberRef }],
      interpretation: '',
    },
  } as unknown as Entity
}

/** A Result with two figure members — used by tests that need to confirm
 *  the × triggers a *standard* removal warning (not the "last member"
 *  block), since there's another non-auto member that would survive. */
function makeResultTwoMembers(refA: string, refB: string): Entity {
  return {
    id: 'res_2',
    type: 'result',
    title: 'Two-member result',
    status: 'active',
    artifact_path: null,
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
    metadata: {
      thread_id: 'thr_t',
      members: [
        { id: 'm_a', kind: 'figure', ref: refA },
        { id: 'm_b', kind: 'figure', ref: refB },
      ],
      interpretation: '',
    },
  } as unknown as Entity
}


describe('ResultView.MemberPanel (redesigned)', () => {
  let origFetch: typeof globalThis.fetch
  beforeEach(() => { origFetch = globalThis.fetch })
  afterEach(() => { globalThis.fetch = origFetch; vi.restoreAllMocks() })

  function installFetchMock(chain: Entity[]) {
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/revisions')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            chain, position: 0, prev: chain[1]?.id ?? null, next: null,
          }),
        } as unknown as Response)
      }
      // Other fetches (PATCH caption, POST members, etc.) → no-op success
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as unknown as Response)
    }) as typeof globalThis.fetch
  }

  function findFigureImg(): HTMLImageElement | null {
    const imgs = document.querySelectorAll('img.rv-panel__img')
    return (imgs[0] as HTMLImageElement) ?? null
  }

  it('displays the LATEST revision\'s image even when member.ref is the oldest', async () => {
    const anchor = fig('fig_anchor', '2026-01-01T00:00:00', 'Anchor')
    const rev2 = fig('fig_rev2', '2026-02-01T00:00:00', 'Rev 2')
    const rev3 = fig('fig_rev3_latest', '2026-03-01T00:00:00', 'Rev 3 latest')
    // Chain order (newest first) matches what /api/entities/{id}/revisions returns.
    installFetchMock([rev3, rev2, anchor])

    render(
      <ResultView
        result={makeResult(anchor.id)}
        entities={[anchor, rev2, rev3]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )
    // Wait for the hook to resolve + state to update
    await waitFor(() => {
      const img = findFigureImg()
      expect(img?.src).toContain(rev3.artifact_path!)
    })
    // Verify the rev label says "rev 3 / 3"
    expect(screen.getByTitle(/Revision 3 of 3/)).toBeTruthy()
  })

  it('clicking ‹ chevron swaps the displayed <img> in place (no nav)', async () => {
    const anchor = fig('fig_anchor', '2026-01-01T00:00:00')
    const rev2 = fig('fig_rev2', '2026-02-01T00:00:00')
    const rev3 = fig('fig_rev3', '2026-03-01T00:00:00')
    installFetchMock([rev3, rev2, anchor])

    let focusCalls = 0
    render(
      <ResultView
        result={makeResult(anchor.id)}
        entities={[anchor, rev2, rev3]}
        onChange={() => {}}
        onFocus={() => { focusCalls++ }}
      />
    )
    await waitFor(() => expect(findFigureImg()?.src).toContain(rev3.artifact_path!))

    // Step back to rev2
    act(() => { fireEvent.click(screen.getByLabelText('Older revision')) })
    await waitFor(() => expect(findFigureImg()?.src).toContain(rev2.artifact_path!))
    // No URL navigation
    expect(focusCalls).toBe(0)

    // Step back to anchor
    act(() => { fireEvent.click(screen.getByLabelText('Older revision')) })
    await waitFor(() => expect(findFigureImg()?.src).toContain(anchor.artifact_path!))
    expect(focusCalls).toBe(0)
  })

  it('Revise from LATEST fires action="revision" with the latest entity id', async () => {
    const anchor = fig('fig_anchor', '2026-01-01T00:00:00')
    const rev2 = fig('fig_rev2_latest', '2026-02-01T00:00:00')
    installFetchMock([rev2, anchor])

    const onChat = vi.fn()
    render(
      <ResultView
        result={makeResult(anchor.id)}
        entities={[anchor, rev2]}
        onChange={() => {}}
        onFocus={() => {}}
        onChatResult={onChat}
      />
    )
    await waitFor(() => expect(findFigureImg()?.src).toContain(rev2.artifact_path!))

    // Open SplitButton dropdown
    fireEvent.click(screen.getByTitle('More actions'))
    const reviseOpt = await screen.findByText('Make a revision')
    fireEvent.click(reviseOpt)
    // No dialog
    expect(screen.queryByRole('dialog')).toBeNull()
    // Called with action='revision', entityId=rev2.id (the displayed/latest)
    expect(onChat).toHaveBeenCalledTimes(1)
    const args = onChat.mock.calls[0]
    expect(args[3]).toBe('revision')
    expect(args[4]).toBe(rev2.id)
  })

  it('Revise from NON-LATEST opens confirmation; Confirm fires "revision-supersede" with the non-latest id', async () => {
    const anchor = fig('fig_anchor', '2026-01-01T00:00:00')
    const rev2 = fig('fig_rev2_latest', '2026-02-01T00:00:00')
    installFetchMock([rev2, anchor])

    const onChat = vi.fn()
    render(
      <ResultView
        result={makeResult(anchor.id)}
        entities={[anchor, rev2]}
        onChange={() => {}}
        onFocus={() => {}}
        onChatResult={onChat}
      />
    )
    await waitFor(() => expect(findFigureImg()?.src).toContain(rev2.artifact_path!))

    // Step back to anchor
    act(() => { fireEvent.click(screen.getByLabelText('Older revision')) })
    await waitFor(() => expect(findFigureImg()?.src).toContain(anchor.artifact_path!))

    // Open the SplitButton dropdown — clicking the chevron should reveal it
    fireEvent.click(screen.getByTitle('More actions'))
    const reviseOpt = await screen.findByText('Make a revision…')
    fireEvent.click(reviseOpt)

    // Confirmation dialog appears
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent?.toLowerCase()).toContain('non-latest')
    // Newer-count language must reflect the actual count
    expect(dialog.textContent).toMatch(/1 newer revision/)
    expect(onChat).not.toHaveBeenCalled()

    // Confirm
    fireEvent.click(screen.getByText('Revise (supersede newer)'))
    expect(onChat).toHaveBeenCalledTimes(1)
    const args = onChat.mock.calls[0]
    expect(args[3]).toBe('revision-supersede')
    expect(args[4]).toBe(anchor.id)
  })

  it('⋯ menu → "Remove from Result" when other members exist → standard remove-confirm dialog', async () => {
    const figA = fig('fig_a', '2026-01-01T00:00:00', 'Figure A')
    const figB = fig('fig_b', '2026-01-02T00:00:00', 'Figure B')
    installFetchMock([figA])  // single-element chain (no revisions)

    const onChange = vi.fn()
    render(
      <ResultView
        result={makeResultTwoMembers(figA.id, figB.id)}
        entities={[figA, figB]}
        onChange={onChange}
        onFocus={() => {}}
      />
    )
    await waitFor(() => {
      const imgs = document.querySelectorAll('img.rv-panel__img')
      expect(imgs.length).toBe(2)
    })
    // Open the first panel's ⋯ menu, then pick "Remove from Result".
    const moreButtons = document.querySelectorAll('.rv-panel__more-btn')
    expect(moreButtons.length).toBe(2)
    fireEvent.click(moreButtons[0])
    fireEvent.click(screen.getByText('Remove from Result'))
    // Standard confirm dialog appears
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent?.toLowerCase()).toContain('remove this figure from the result')
    expect(dialog.textContent).toMatch(/Figure A/)
    expect(dialog.textContent?.toLowerCase()).toContain('revisions')
    expect(dialog.textContent?.toLowerCase()).toContain('not deleted')
    // Cancel doesn't fire
    fireEvent.click(screen.getByText('Cancel'))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(globalThis.fetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/members/'),
      expect.objectContaining({ method: 'DELETE' })
    )
  })

  it('⋯ menu → "Remove from Result" on the ONLY meaningful member → BLOCKING info dialog directing to rail ⋯ menu', async () => {
    const figOnly = fig('fig_only', '2026-01-01T00:00:00', 'Solo Figure')
    installFetchMock([figOnly])
    render(
      <ResultView
        result={makeResult(figOnly.id)}
        entities={[figOnly]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )
    await waitFor(() => {
      const img = document.querySelector('img.rv-panel__img') as HTMLImageElement | null
      expect(img?.src).toContain(figOnly.artifact_path!)
    })
    const moreBtn = document.querySelector('.rv-panel__more-btn') as HTMLButtonElement
    fireEvent.click(moreBtn)
    fireEvent.click(screen.getByText('Remove from Result'))
    const dialog = await screen.findByRole('dialog')
    // Info dialog (no Cancel button) — directs to ⋯ menu
    expect(dialog.textContent?.toLowerCase()).toContain('empty the result')
    expect(dialog.textContent).toMatch(/⋯ menu/)
    expect(dialog.textContent?.toLowerCase()).toContain('equivalent to deleting')
    // No Cancel button — only acknowledgement
    expect(screen.queryByText('Cancel')).toBeNull()
    expect(screen.getByText('Got it')).toBeTruthy()
    fireEvent.click(screen.getByText('Got it'))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('confirming "Remove" in the standard dialog actually issues the DELETE', async () => {
    const figA = fig('fig_a', '2026-01-01T00:00:00')
    const figB = fig('fig_b', '2026-01-02T00:00:00')
    installFetchMock([figA])
    render(
      <ResultView
        result={makeResultTwoMembers(figA.id, figB.id)}
        entities={[figA, figB]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )
    await waitFor(() => {
      const imgs = document.querySelectorAll('img.rv-panel__img')
      expect(imgs.length).toBe(2)
    })
    // Open the first panel's ⋯ menu, then "Remove from Result", then "Remove".
    fireEvent.click(document.querySelectorAll('.rv-panel__more-btn')[0])
    fireEvent.click(screen.getByText('Remove from Result'))
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByText('Remove'))
    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/results/res_2/members/m_a'),
        expect.objectContaining({ method: 'DELETE' })
      )
    })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('⋯ menu HIDES "Remove this version" on a single-entry chain', async () => {
    const figOnly = fig('fig_only', '2026-01-01T00:00:00', 'Solo Figure')
    installFetchMock([figOnly])
    render(
      <ResultView
        result={makeResult(figOnly.id)}
        entities={[figOnly]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )
    await waitFor(() => {
      const img = document.querySelector('img.rv-panel__img') as HTMLImageElement | null
      expect(img?.src).toContain(figOnly.artifact_path!)
    })
    const moreBtn = document.querySelector('.rv-panel__more-btn') as HTMLButtonElement
    fireEvent.click(moreBtn)
    // Only "Remove from Result" is offered. "Remove this version" must
    // not appear (chain has just one entry — version-delete would empty
    // the chain).
    expect(screen.queryByText('Remove this version')).toBeNull()
    expect(screen.getByText('Remove from Result')).toBeTruthy()
  })

  it('⋯ menu SHOWS "Remove this version" on a 2-entry chain → confirm fires POST /delete-revision', async () => {
    const anchor = fig('fig_anchor_dv', '2026-01-01T00:00:00', 'Anchor v1')
    const rev2 = fig('fig_rev2_dv', '2026-02-01T00:00:00', 'Revision v2')
    // The chain mock returns 2 entries: latest (rev2) first, anchor last.
    installFetchMock([rev2, anchor])

    // Track POSTs to /delete-revision
    const deleteCalls: string[] = []
    const baseFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (typeof url === 'string'
          && url.includes('/delete-revision')
          && init?.method === 'POST') {
        deleteCalls.push(url)
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ deleted: rev2.id, new_anchor: anchor.id }),
        } as unknown as Response)
      }
      return baseFetch(url, init)
    }) as typeof globalThis.fetch

    render(
      <ResultView
        result={makeResult(anchor.id)}
        entities={[anchor, rev2]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )
    // Wait for the chevrons (proves chain rendered with 2 entries)
    await waitFor(() => {
      expect(screen.getByLabelText('Older revision')).toBeTruthy()
    })
    // Displayed defaults to the LATEST (rev2)
    await waitFor(() => {
      const img = document.querySelector('img.rv-panel__img') as HTMLImageElement | null
      expect(img?.src).toContain(rev2.artifact_path!)
    })
    // Open ⋯ — both items present
    fireEvent.click(document.querySelector('.rv-panel__more-btn')!)
    expect(screen.getByText('Remove this version')).toBeTruthy()
    expect(screen.getByText('Remove from Result')).toBeTruthy()
    fireEvent.click(screen.getByText('Remove this version'))
    // Destructive confirm dialog appears, naming the version
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent?.toLowerCase()).toContain('permanently')
    expect(dialog.textContent).toMatch(/Revision v2/)
    // Cancel doesn't fire
    fireEvent.click(screen.getByText('Cancel'))
    expect(deleteCalls).toHaveLength(0)

    // Re-open menu, pick version-delete again, this time confirm
    fireEvent.click(document.querySelector('.rv-panel__more-btn')!)
    fireEvent.click(screen.getByText('Remove this version'))
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByText('Remove this version'))
    await waitFor(() => {
      expect(deleteCalls).toHaveLength(1)
      expect(deleteCalls[0]).toContain(`/api/entities/${rev2.id}/delete-revision`)
    })
  })

  // The "live agent revises in background" bug: ResultView mounts with
  // chain of 1 (no chevrons). Agent calls make_revision; backend creates
  // a new entity + broadcasts entity_updated; App.refresh() updates the
  // entities prop. The chevrons MUST appear without a page reload —
  // useFigureHistory must re-fetch when the entities-derived signal
  // bumps. This is the exact regression the user hit on 2026-06-07.
  it('LIVE: agent adds a revision in background → chevrons appear without reload', async () => {
    const anchor = fig('fig_anchor', '2026-01-01T00:00:00', 'Anchor')
    const newRev = fig('fig_rev_new', '2026-02-01T00:00:00', 'Rev 2')
    // Important: the "new revision" entity carries metadata.revision_of
    // — that's the marker the ResultView counts to derive revisionsSignal.
    ;(newRev as unknown as { metadata: { revision_of: string } }).metadata = { revision_of: anchor.id }

    // Mock /revisions to return whatever chain is currently "real" at fetch time.
    // We mutate `liveChain` between renders to simulate the backend updating.
    let liveChain: Entity[] = [anchor]
    let fetchCalls = 0
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/revisions')) {
        fetchCalls++
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            chain: liveChain, position: 0,
            prev: liveChain[1]?.id ?? null, next: null,
          }),
        } as unknown as Response)
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as unknown as Response)
    }) as typeof globalThis.fetch

    // 1) Initial render: only the anchor exists. No chevrons.
    const { rerender } = render(
      <ResultView
        result={makeResult(anchor.id)}
        entities={[anchor]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )
    await waitFor(() => {
      const img = document.querySelector('img.rv-panel__img') as HTMLImageElement | null
      expect(img?.src).toContain(anchor.artifact_path!)
    })
    expect(screen.queryByLabelText('Older revision')).toBeNull()
    expect(screen.queryByLabelText('Newer revision')).toBeNull()
    const initialFetches = fetchCalls

    // 2) Simulate the agent finishing make_revision:
    //    backend now returns chain of 2, AND entities prop grows.
    liveChain = [newRev, anchor]
    rerender(
      <ResultView
        result={makeResult(anchor.id)}
        entities={[anchor, newRev]}  // <-- the new revision is now in the project
        onChange={() => {}}
        onFocus={() => {}}
      />
    )

    // 3) The hook MUST re-fetch (revisionsSignal bumped from 0 → 1)
    //    and the chevrons MUST appear pointing at the new revision.
    await waitFor(() => {
      expect(screen.getByLabelText('Older revision')).toBeTruthy()
    })
    // The displayed image swaps to the new (latest) revision automatically
    await waitFor(() => {
      const img = document.querySelector('img.rv-panel__img') as HTMLImageElement | null
      expect(img?.src).toContain(newRev.artifact_path!)
    })
    expect(screen.getByTitle(/Revision 2 of 2/).textContent).toContain('latest')
    // And we actually issued a second fetch (not stuck on cached response)
    expect(fetchCalls).toBeGreaterThan(initialFetches)
  })

  // Same regression in supersede shape: chain grows, then collapses
  // (newer entries flipped to status='superseded'); the visible chain
  // length stays 2 but the head identity changes — the panel must
  // update without reload.
  it('LIVE: agent supersedes then revises → chevrons + displayed image both update', async () => {
    const anchor = fig('fig_anchor', '2026-01-01T00:00:00', 'Anchor')
    const rev2 = fig('fig_rev2_super', '2026-02-01T00:00:00', 'Rev 2 (will be superseded)')
    ;(rev2 as unknown as { metadata: { revision_of: string } }).metadata = { revision_of: anchor.id }
    const rev3 = fig('fig_rev3_new', '2026-03-01T00:00:00', 'Rev 3 from anchor')
    ;(rev3 as unknown as { metadata: { revision_of: string } }).metadata = { revision_of: anchor.id }

    let liveChain: Entity[] = [rev2, anchor]
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/revisions')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            chain: liveChain, position: 0,
            prev: liveChain[1]?.id ?? null, next: null,
          }),
        } as unknown as Response)
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as unknown as Response)
    }) as typeof globalThis.fetch

    const { rerender } = render(
      <ResultView
        result={makeResult(anchor.id)}
        entities={[anchor, rev2]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )
    await waitFor(() => {
      const img = document.querySelector('img.rv-panel__img') as HTMLImageElement | null
      expect(img?.src).toContain(rev2.artifact_path!)
    })

    // Agent: supersede rev2, create rev3 from anchor. Backend's chain
    // walk filters superseded → returns [rev3, anchor]. entities grows.
    liveChain = [rev3, anchor]
    rerender(
      <ResultView
        result={makeResult(anchor.id)}
        entities={[anchor, rev2, rev3]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )

    await waitFor(() => {
      const img = document.querySelector('img.rv-panel__img') as HTMLImageElement | null
      expect(img?.src).toContain(rev3.artifact_path!)
    })
  })

  // PDF figure with derived preview: img must use preview_path, and a
  // visible "↓ PDF" chip must offer the canonical for download. Tests
  // the artifact-as-truth model where the panel preserves fidelity by
  // rendering a backend-rasterized PNG of the actual canonical PDF.
  it('PDF canonical with preview_path → img uses preview, "↓ PDF" chip downloads canonical', async () => {
    const pdf: Entity = {
      ...fig('fig_pdf', '2026-01-01T00:00:00', 'PDF UMAP'),
      artifact_path: '/artifacts/prj_x/umap.pdf',
    } as unknown as Entity
    ;(pdf as unknown as { metadata: object }).metadata = {
      preview_path: '/artifacts/prj_x/umap.pdf.preview.png',
    }
    installFetchMock([pdf])
    render(
      <ResultView
        result={makeResult(pdf.id)}
        entities={[pdf]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )
    await waitFor(() => {
      const img = document.querySelector('img.rv-panel__img') as HTMLImageElement | null
      expect(img).not.toBeNull()
      // <img> should point at the PNG preview, not the PDF
      expect(img!.src).toContain('umap.pdf.preview.png')
      expect(img!.src).not.toMatch(/umap\.pdf$/)
    })
    // Download chip is rendered, with the canonical href + a meaningful label
    const dl = document.querySelector('a.rev-strip__download') as HTMLAnchorElement | null
    expect(dl).not.toBeNull()
    expect(dl!.getAttribute('href')).toContain('umap.pdf')
    expect(dl!.getAttribute('href')!.endsWith('.pdf')).toBe(true)
    expect(dl!.textContent).toMatch(/PDF/)
    // The 'download' attribute is set so browsers prefer save-as over open
    expect(dl!.hasAttribute('download')).toBe(true)
  })

  it('PNG canonical (no preview_path) → img uses canonical, no download chip', async () => {
    const png = fig('fig_png_only', '2026-01-01T00:00:00')
    installFetchMock([png])
    render(
      <ResultView
        result={makeResult(png.id)}
        entities={[png]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )
    await waitFor(() => {
      const img = document.querySelector('img.rv-panel__img') as HTMLImageElement | null
      expect(img?.src).toContain(png.artifact_path!)
    })
    expect(document.querySelector('a.rev-strip__download')).toBeNull()
  })

  it('renders no chevrons (and no revision label) when chain length is 1', async () => {
    const single = fig('fig_only', '2026-01-01T00:00:00')
    installFetchMock([single])
    render(
      <ResultView
        result={makeResult(single.id)}
        entities={[single]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )
    await waitFor(() => expect(findFigureImg()?.src).toContain(single.artifact_path!))
    expect(screen.queryByLabelText('Older revision')).toBeNull()
    // SplitButton still present
    expect(screen.getByRole('button', { name: /💬 Chat/ })).toBeTruthy()
  })

  // The "agent updates caption in background" regression: backend
  // wrote the new caption + broadcast entity_updated; App.refresh()
  // updated the entities prop with the new member.caption. The
  // <textarea> MUST pick it up without a browser reload. Pre-2026-06-11
  // the picker effect's condition was `prev === '' || prev ===
  // member.caption`, which compared local state against the NEW prop —
  // so once local had ever held the OLD value it stayed pinned to it.
  it('LIVE: agent updates member.caption in background → textarea picks it up', async () => {
    const anchor = fig('fig_anchor', '2026-01-01T00:00:00', 'Anchor')
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/revisions')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            chain: [anchor], position: 0, prev: null, next: null,
          }),
        } as unknown as Response)
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as unknown as Response)
    }) as typeof globalThis.fetch

    // 1) Initial render: Result has the old auto-caption on its single member.
    const resultWithCaption = (cap: string): Entity => ({
      id: 'res_cap',
      type: 'result',
      title: 'Caption regression',
      status: 'active',
      artifact_path: null,
      created_at: '2026-01-01T00:00:00',
      updated_at: '2026-01-01T00:00:00',
      metadata: {
        thread_id: 'thr_t',
        members: [{ id: 'm_1', kind: 'figure', ref: anchor.id,
                     caption: cap, caption_origin: 'ai' }],
        interpretation: '',
      },
    } as unknown as Entity)

    const { rerender } = render(
      <ResultView
        result={resultWithCaption('old auto caption')}
        entities={[anchor]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )
    await waitFor(() => {
      const ta = document.querySelector('textarea.rv-panel__caption') as HTMLTextAreaElement | null
      expect(ta?.value).toBe('old auto caption')
    })

    // 2) Agent updates the caption; App refetches entities; prop changes.
    rerender(
      <ResultView
        result={resultWithCaption('new caption from agent')}
        entities={[anchor]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )

    // 3) The textarea MUST reflect the new value without a browser refresh.
    await waitFor(() => {
      const ta = document.querySelector('textarea.rv-panel__caption') as HTMLTextAreaElement | null
      expect(ta?.value).toBe('new caption from agent')
    })
  })

  // Regression guard for the in-flight edit protection — flipping the
  // picker effect must NOT clobber a user mid-typing. Same setup as
  // above but with a user edit between the two renders.
  it('does NOT clobber an in-flight user edit when server caption changes', async () => {
    const anchor = fig('fig_anchor', '2026-01-01T00:00:00', 'Anchor')
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/revisions')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            chain: [anchor], position: 0, prev: null, next: null,
          }),
        } as unknown as Response)
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as unknown as Response)
    }) as typeof globalThis.fetch

    const resultWithCaption = (cap: string): Entity => ({
      id: 'res_cap2',
      type: 'result',
      title: 'In-flight edit guard',
      status: 'active',
      artifact_path: null,
      created_at: '2026-01-01T00:00:00',
      updated_at: '2026-01-01T00:00:00',
      metadata: {
        thread_id: 'thr_t',
        members: [{ id: 'm_1', kind: 'figure', ref: anchor.id,
                     caption: cap, caption_origin: 'ai' }],
        interpretation: '',
      },
    } as unknown as Entity)

    const { rerender } = render(
      <ResultView
        result={resultWithCaption('initial caption')}
        entities={[anchor]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )
    const ta = await waitFor(() => {
      const t = document.querySelector('textarea.rv-panel__caption') as HTMLTextAreaElement
      expect(t.value).toBe('initial caption')
      return t
    })

    // User starts editing — local state diverges from the prop.
    fireEvent.change(ta, { target: { value: 'user typing…' } })
    expect((document.querySelector('textarea.rv-panel__caption') as HTMLTextAreaElement).value)
      .toBe('user typing…')

    // Meanwhile auto_interpret server-side overwrites the caption. The
    // user has NOT blurred yet, so their edit must survive.
    rerender(
      <ResultView
        result={resultWithCaption('autodaemon rewrite')}
        entities={[anchor]}
        onChange={() => {}}
        onFocus={() => {}}
      />
    )
    // A brief wait to let any straggling effects fire.
    await new Promise(r => setTimeout(r, 30))
    expect((document.querySelector('textarea.rv-panel__caption') as HTMLTextAreaElement).value)
      .toBe('user typing…')
  })
})
