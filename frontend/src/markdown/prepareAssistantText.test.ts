/** Fix #4 — prepareAssistantText turns <reasoning>/<thinking> blocks into
 * blockquotes so ReactMarkdown renders them visibly instead of either
 * silently dropping the content or tripping the ErrorBoundary.
 */
import { describe, it, expect } from 'vitest'
import { prepareAssistantText } from './prepareAssistantText'


describe('prepareAssistantText', () => {
  it('passes plain text through unchanged', () => {
    expect(prepareAssistantText('hello world')).toBe('hello world')
  })

  it('returns empty for null/undefined/empty', () => {
    expect(prepareAssistantText('')).toBe('')
    expect(prepareAssistantText(undefined)).toBe('')
    expect(prepareAssistantText(null)).toBe('')
  })

  it('converts a closed <reasoning>...</reasoning> to a blockquote', () => {
    const input = '<reasoning>\nThe job seems to have failed.\n</reasoning>\nLet me check.'
    const out = prepareAssistantText(input)
    expect(out).toContain('> _Reasoning')
    expect(out).toContain('> The job seems to have failed.')
    // The trailing prose is preserved
    expect(out).toContain('Let me check.')
    // Raw tags are gone
    expect(out).not.toContain('<reasoning>')
    expect(out).not.toContain('</reasoning>')
  })

  it('converts <thinking>...</thinking> similarly', () => {
    const out = prepareAssistantText('<thinking>step 1\nstep 2</thinking>')
    expect(out).toContain('> _Thinking')
    expect(out).toContain('> step 1')
    expect(out).toContain('> step 2')
  })

  it('handles unterminated tag (mid-stream): partial blockquote to end of text', () => {
    const out = prepareAssistantText('<reasoning>\nI need to check the log first')
    expect(out).toContain('> _Reasoning')
    expect(out).toContain('> I need to check the log first')
    expect(out).not.toContain('<reasoning>')
  })

  it('drops orphan close tag defensively', () => {
    const out = prepareAssistantText('Some text </reasoning> more text')
    expect(out).not.toContain('</reasoning>')
    expect(out).toContain('Some text')
    expect(out).toContain('more text')
  })

  it('preserves <reasoning> inside fenced code blocks', () => {
    const code = '```html\n<reasoning>example</reasoning>\n```'
    const out = prepareAssistantText(code)
    // Inside ```, the tag is preserved verbatim so authors can document XML/HTML
    expect(out).toContain('<reasoning>example</reasoning>')
  })

  it('handles multiple reasoning blocks independently', () => {
    const input = '<reasoning>first</reasoning>\n\nText.\n\n<reasoning>second</reasoning>'
    const out = prepareAssistantText(input)
    // Both got converted
    expect(out).not.toContain('<reasoning>')
    // Both bodies present
    expect(out).toContain('> first')
    expect(out).toContain('> second')
  })

  it('handles mixed reasoning + thinking', () => {
    const input = '<reasoning>r1</reasoning>\n<thinking>t1</thinking>'
    const out = prepareAssistantText(input)
    expect(out).toContain('> _Reasoning')
    expect(out).toContain('> _Thinking')
    expect(out).toContain('> r1')
    expect(out).toContain('> t1')
  })

  it('empty reasoning block produces empty replacement (no rogue blockquote)', () => {
    const out = prepareAssistantText('<reasoning></reasoning>after')
    expect(out).not.toContain('<reasoning>')
    expect(out).not.toContain('> _Reasoning')   // no header for empty body
    expect(out).toContain('after')
  })
})
