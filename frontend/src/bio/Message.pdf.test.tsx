/**
 * Tests for the PDF rendering path in chat messages (regression guard
 * for the broken-image bug introduced by Phase 2 of #421: single-page
 * PDFs now arrive in plots[] but the browser can't <img src=*.pdf>).
 *
 * Two contracts:
 *   1. ImageBlock with preview_url → <img> uses preview_url (the
 *      rasterized .preview.png), NOT the canonical PDF URL.
 *   2. Markdown link override → /artifacts/*.pdf links carry a
 *      `download` attribute derived from the visible link text so
 *      save-as uses the human filename instead of the content hash.
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import Message from './Message'
import type { Message as Msg, Block } from '../types'


describe('Message — PDF chat-render path', () => {
  it('uses preview_url for <img src> when set (canonical is PDF, preview is PNG)', () => {
    const blocks: Block[] = [
      { type: 'image',
        url: '/artifacts/prj_x/abc.pdf',
        preview_url: '/artifacts/prj_x/abc.pdf.preview.png',
        alt: 'marker_heatmap_native.pdf' },
    ]
    const m: Msg = { id: 'm1', role: 'assistant', blocks }
    const { container } = render(
      <Message message={m} entities={[]} pinnedFigureIds={new Set()} keptKeys={new Set()} />
    )
    const imgs = container.querySelectorAll('img.msg-image__img')
    expect(imgs.length).toBe(1)
    const src = imgs[0].getAttribute('src') || ''
    expect(src).toContain('abc.pdf.preview.png')   // preview, not .pdf
    expect(src).not.toMatch(/abc\.pdf$/)            // explicitly not the canonical
  })

  it('falls back to canonical url when preview_url is absent (PNG case)', () => {
    const blocks: Block[] = [
      { type: 'image',
        url: '/artifacts/prj_x/p.png',
        alt: 'p.png' },
    ]
    const m: Msg = { id: 'm1', role: 'assistant', blocks }
    const { container } = render(
      <Message message={m} entities={[]} pinnedFigureIds={new Set()} keptKeys={new Set()} />
    )
    const img = container.querySelector('img.msg-image__img') as HTMLImageElement | null
    expect(img?.getAttribute('src')).toContain('p.png')
  })

  it('markdown /artifacts/ link gets target=_blank and download attr from visible text', () => {
    const blocks: Block[] = [
      { type: 'text',
        text: 'Open [Open umap_leiden.pdf](/artifacts/prj_x/183906e6f9394fd080f79286bc46b35b.pdf)' },
    ]
    const m: Msg = { id: 'm2', role: 'assistant', blocks }
    const { container } = render(
      <Message message={m} entities={[]} pinnedFigureIds={new Set()} keptKeys={new Set()} />
    )
    const a = container.querySelector('a[href*="/artifacts/"]') as HTMLAnchorElement | null
    expect(a).not.toBeNull()
    expect(a!.getAttribute('target')).toBe('_blank')
    // download attr derives "umap_leiden.pdf" from the visible link
    // text, NOT the content-hash basename of the URL. This is what
    // makes "Save as" produce a meaningful filename instead of
    // "183906e6...pdf".
    expect(a!.getAttribute('download')).toBe('umap_leiden.pdf')
  })

  it('markdown non-artifact links get no download attr', () => {
    const blocks: Block[] = [
      { type: 'text',
        text: '[Anthropic docs](https://docs.anthropic.com/x)' },
    ]
    const m: Msg = { id: 'm3', role: 'assistant', blocks }
    const { container } = render(
      <Message message={m} entities={[]} pinnedFigureIds={new Set()} keptKeys={new Set()} />
    )
    const a = container.querySelector('a[href*="anthropic.com"]') as HTMLAnchorElement | null
    expect(a).not.toBeNull()
    expect(a!.hasAttribute('download')).toBe(false)
    expect(a!.getAttribute('target')).toBeNull()
  })
})
