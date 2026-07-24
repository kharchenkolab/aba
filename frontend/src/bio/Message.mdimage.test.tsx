/**
 * Regression guard: a stray markdown image (`![](…)`) — which the agent
 * occasionally emits for a figure despite the steering (see figures.md) —
 * must render through the same ZoomableImg wrapper as a tool-result figure:
 * scaled to the column (the `.msg-text img` CSS rule) and click-to-lightbox.
 * Before the fix it rendered as a bare, unclassed <img> that overflowed the
 * chat column and had no lightbox. This is the defensive-render half of the
 * fix; the prompt steering is the other half.
 */
import { describe, it, expect } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import Message from './Message'
import type { Message as Msg, Block } from '../types'


describe('Message — stray markdown image render path', () => {
  it('markdown ![](url) renders through ZoomableImg (.msg-image__img), not a bare <img>', () => {
    const blocks: Block[] = [
      { type: 'text', text: 'Here is the plot:\n\n![UMAP](/artifacts/prj_x/umap.png)' },
    ]
    const m: Msg = { id: 'm1', role: 'assistant', blocks }
    const { container } = render(
      <Message message={m} entities={[]} pinnedFigureIds={new Set()} keptKeys={new Set()} />
    )
    const img = container.querySelector('.msg-text img') as HTMLImageElement | null
    expect(img).not.toBeNull()
    // Routed through ZoomableImg → carries the scaled/lightbox class, so the
    // `.msg-text img { max-width:100% }` rule applies and it can't overflow.
    expect(img!.className).toContain('msg-image__img')
    expect(img!.getAttribute('src')).toContain('umap.png')
  })

  it('clicking the markdown image opens the lightbox (portaled to <body>)', () => {
    const blocks: Block[] = [
      { type: 'text', text: '![UMAP](/artifacts/prj_x/umap.png)' },
    ]
    const m: Msg = { id: 'm2', role: 'assistant', blocks }
    const { container } = render(
      <Message message={m} entities={[]} pinnedFigureIds={new Set()} keptKeys={new Set()} />
    )
    expect(document.querySelector('.lightbox')).toBeNull()
    fireEvent.click(container.querySelector('.msg-text img') as HTMLElement)
    // The lightbox is portaled to document.body, so query the whole document.
    expect(document.querySelector('.lightbox')).not.toBeNull()
    expect(document.querySelector('.lightbox__img')).not.toBeNull()
  })
})
