/** Pre-process assistant text before it goes to ReactMarkdown.
 *
 * The agent occasionally emits `<reasoning>...</reasoning>` or
 * `<thinking>...</thinking>` blocks as part of its text output — Claude's
 * fallback when given a confusing context (see misc/recovery / live-session
 * analysis 2026-06-08). These look like raw HTML to react-markdown and
 * either render as nothing (silently stripping content the user might
 * want to see) or trip the ErrorBoundary on a partial-stream tag, giving
 * a generic "couldn't be displayed" notice.
 *
 * Solution: convert the tagged blocks to Markdown blockquotes so the
 * content renders visibly, framed as model scratchpad. Handles three
 * shapes:
 *
 *   1. `<reasoning>...</reasoning>` — closed pair: full blockquote.
 *   2. `<reasoning>...` (unterminated, streaming): partial blockquote
 *      up to the end of the text.
 *   3. `</reasoning>` orphan: dropped (defensive).
 *
 * Inside fenced code blocks the transformation is skipped — code may
 * legitimately contain `<x>` strings.
 */
const KNOWN_TAGS = ['reasoning', 'thinking'] as const

export function prepareAssistantText(raw: string | undefined | null): string {
  if (!raw) return ''
  // Split on triple-backtick fences so we don't transform inside code.
  // Even-indexed parts are prose; odd-indexed are fenced bodies.
  const parts = raw.split(/(```[\s\S]*?```)/g)
  for (let i = 0; i < parts.length; i += 2) {
    parts[i] = transformProse(parts[i])
  }
  return parts.join('')
}

function transformProse(text: string): string {
  let out = text
  for (const tag of KNOWN_TAGS) {
    const label = tag.charAt(0).toUpperCase() + tag.slice(1)
    // 1. Closed pairs → blockquote
    const closed = new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`, 'g')
    out = out.replace(closed, (_m, body) => quoted(label, body))
    // 2. Unterminated opening tag at end of stream → partial blockquote
    const unterminated = new RegExp(`<${tag}>([\\s\\S]*)$`)
    out = out.replace(unterminated, (_m, body) => quoted(label, body))
    // 3. Orphan close → drop (defensive; shouldn't survive the closed-pair pass)
    const orphan = new RegExp(`<\\/${tag}>`, 'g')
    out = out.replace(orphan, '')
  }
  return out
}

function quoted(label: string, body: string): string {
  const trimmed = body.trim()
  if (!trimmed) return ''
  const inner = trimmed.split('\n').map(l => l.length ? `> ${l}` : '>').join('\n')
  return `\n\n> _${label} (model's scratchpad):_\n${inner}\n\n`
}
