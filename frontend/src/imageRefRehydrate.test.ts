// Reload rehydration of vision refs (useChat.blocksFromContent).
//
// Durable history stores image_ref blocks instead of base64 payloads
// (backend vision_refs). The transcript renderer used to match only
// text/image block types, so on thread reload every viewed image silently
// vanished — no image, no placeholder. These guards pin the rehydration
// contract: entity-backed refs render as served images, path-only refs get
// a labeled placeholder, and the legacy inline-base64 shape keeps working.
import { describe, expect, it } from 'vitest'
import { blocksFromContent } from './useChat'

function toolResult(content: unknown) {
  return [{ type: 'tool_result', content } as unknown as Record<string, unknown>]
}

describe('image_ref rehydration on thread reload', () => {
  it('entity-backed ref renders as a served image, not a silent drop', () => {
    const blocks = blocksFromContent(toolResult([
      { type: 'text', text: 'Image fig.png:' },
      { type: 'image_ref', tool: 'view_artifact', entity_id: 'ent_1',
        path: '/artifacts/p1/fig.png', media_type: 'image/png' },
    ]))
    const img = blocks.find(b => b.type === 'image') as { url?: string } | undefined
    expect(img, 'image_ref dropped from transcript').toBeTruthy()
    expect(img!.url).toContain('/api/entities/ent_1/download')
  })

  it('path-only ref gets a labeled placeholder, never nothing', () => {
    const blocks = blocksFromContent(toolResult([
      { type: 'image_ref', tool: 'view_file', path: '/work/plot.png',
        media_type: 'image/png' },
    ]))
    const flat = JSON.stringify(blocks)
    expect(flat).toContain('plot.png')
    expect(blocks.some(b => b.type === 'image')).toBe(false)
  })

  it('legacy inline base64 still renders (the other side)', () => {
    const blocks = blocksFromContent(toolResult([
      { type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'AAAA' } },
    ]))
    const img = blocks.find(b => b.type === 'image') as { url?: string } | undefined
    expect(img).toBeTruthy()
    expect(img!.url).toContain('data:image/png;base64,AAAA')
  })
})
