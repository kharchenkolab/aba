/**
 * Shared primitives for the freehand-highlight gesture.
 *
 * Origin: lifted from Message.tsx so ResultView (and other cell-bearing
 * surfaces) can reuse the same draw-then-rasterize-then-attach flow.
 * Pure functions only — no React, no state. The caller manages stroke
 * state + the hover surface, then hands the captured stroke here.
 *
 * The flow:
 *   1. Caller renders an absolute overlay over a cell while the global
 *      highlight mode is on; on drag, builds a Pt[] of normalized
 *      [0,1] coordinates inside the cell element's bbox.
 *   2. On mouseup, caller invokes `captureHighlight()`.
 *   3. We hit-test "subcards" (CSS selector the caller supplies — for
 *      Messages: `.msg-image, .tool-line, …`; for Result members:
 *      `.rv-panel__cell, .rv-panel__caption, .rv-panel__note`), tight-crop
 *      around the touched ones (or the stroke bbox if none), rasterize
 *      with html2canvas, composite the yellow stroke onto the crop,
 *      and return {image (base64), note (structured prose)}.
 */
import type { Entity } from '../types'

export const HILITE = 'rgba(253, 224, 71, 0.55)'   // highlighter yellow

export type Pt = { x: number; y: number }

/** A process-unique token identifying one highlight capture. The captured
 *  mark stays "frozen" on its cell until superseded or dismissed; App tracks
 *  the current owner by this token, and each cell shows its frozen overlay
 *  only while it holds the live token. Shared across surfaces (chat cells,
 *  Result panels) so tokens never collide between them. */
let _annotSeq = 0
export const nextAnnotToken = (): string => `hl${++_annotSeq}`


/** Concrete shape + position descriptor for a freehand stroke. All math on
 *  normalized (0-1) coords; no DOM access. Stable language the agent can
 *  ground deixis on. */
export function describeStroke(pts: Pt[]): string {
  if (pts.length < 2) return 'a small mark'
  const xs = pts.map(p => p.x), ys = pts.map(p => p.y)
  const xmin = Math.min(...xs), xmax = Math.max(...xs)
  const ymin = Math.min(...ys), ymax = Math.max(...ys)
  const w = xmax - xmin, h = ymax - ymin
  const cx = (xmin + xmax) / 2, cy = (ymin + ymax) / 2
  let pathLen = 0
  for (let i = 1; i < pts.length; i++) {
    pathLen += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y)
  }
  const endGap = Math.hypot(pts[0].x - pts[pts.length - 1].x,
                            pts[0].y - pts[pts.length - 1].y)
  const closed = pathLen > 0.1 && endGap / pathLen < 0.15
  const shape =
    closed                            ? 'a closed loop circling'
    : (h < 0.05 && w > 0.1)           ? 'a horizontal underline across'
    : (w < 0.05 && h > 0.1)           ? 'a vertical mark down'
    : (pathLen < 0.15)                ? 'a small mark on'
                                      : 'an open stroke across'
  const col = cx < 0.33 ? 'left' : cx < 0.67 ? 'center' : 'right'
  const row = cy < 0.33 ? 'top'  : cy < 0.67 ? 'middle' : 'bottom'
  const quadrant =
    (row === 'middle' && col === 'center') ? 'the center'
    : (row === 'middle')                   ? `the ${col} side`
    : (col === 'center')                   ? `the ${row} edge`
                                           : `the ${row}-${col} region`
  const areaPct = Math.max(1, Math.round(w * h * 100))
  return `${shape} ${quadrant} of the figure (the marked region covers ~${areaPct}% of the cell area)`
}


/** If the highlighted cell contains an <img> tied to a figure entity, return
 *  a short reference for the agent ("The figure is 'GSM5746260: UMAP by
 *  Cluster' (fig_abc12)."). Falls back to empty string when no match — the
 *  caller appends its own fallback descriptor. */
export function describeHighlightedFigure(cellEl: HTMLElement | null,
                                          entities: Entity[] | undefined): string {
  if (!cellEl || !entities || entities.length === 0) return ''
  const imgs = cellEl.querySelectorAll('img[src]')
  for (const img of Array.from(imgs)) {
    const src = (img as HTMLImageElement).getAttribute('src') || ''
    const hit = entities.find(e =>
      e.type === 'figure' && typeof e.artifact_path === 'string' && src.endsWith(e.artifact_path))
    if (hit) return `The marked figure is "${hit.title}" (${hit.id}).`
  }
  return ''
}


/** Default subcard labeller — recognizes the chat-cell class names.
 *  ResultView passes its own labeller via captureHighlight's
 *  `describeSubcard` arg. */
export function describeSubcardDefault(sc: HTMLElement): string {
  if (sc.classList.contains('msg-image')) {
    const title = sc.querySelector('.msg-image__title')?.textContent?.trim()
    return title ? `figure "${title}"` : 'a figure'
  }
  if (sc.classList.contains('tool-line')) {
    const label = sc.querySelector('.tool-line__label')?.textContent?.trim()
    return label ? `tool step "${label}"` : 'a tool step'
  }
  if (sc.classList.contains('plan-card')) {
    const head = sc.querySelector('.plan-card__head')?.textContent?.trim()
    return head ? `plan card "${head}"` : 'the plan card'
  }
  if (sc.classList.contains('msg-error')) return 'an error notice'
  if (sc.classList.contains('msg-notice')) return 'a notice'
  if (sc.classList.contains('msg-text')) {
    const txt = (sc.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80)
    return txt ? `text "${txt}${txt.length >= 80 ? '…' : ''}"` : 'a text block'
  }
  return ''
}


type CaptureOpts = {
  cellEl: HTMLElement
  /** Normalized [0,1] stroke points relative to `cellEl`'s bbox. */
  ptsNorm: Pt[]
  /** CSS selector for the "subcards" (clickable content units) within the
   *  cell. The capture clips to the union of touched subcards. */
  subcardSelector: string
  /** Per-cell-type labeller. Defaults to the chat-cell labeller. */
  describeSubcard?: (sc: HTMLElement) => string
  /** For describeHighlightedFigure's lookup. */
  entities?: Entity[]
  /** Cell's textual content — included in the note when no subcard was
   *  touched, so the agent sees "the highlighted message text: '…'". */
  cellText?: string
  /** When true, the cell is image-only (no prose); changes the target
   *  noun from 'message' → 'figure' when no subcards were touched. */
  onlyImage?: boolean
}


/** The heavy lifting: rasterize the cell, crop to touched subcards, composite
 *  the yellow stroke, and assemble the agent-facing note. Returns null when
 *  the stroke was too short (<2 pts) or the rasterizer failed. */
export async function captureHighlight(opts: CaptureOpts):
    Promise<{ image: string; note: string } | null> {
  const { cellEl, ptsNorm, subcardSelector, entities, cellText, onlyImage } = opts
  const describeSubcard = opts.describeSubcard ?? describeSubcardDefault
  if (ptsNorm.length < 2) return null

  const elRect = cellEl.getBoundingClientRect()
  const strokeLocal = ptsNorm.map(p => ({ x: p.x * elRect.width, y: p.y * elRect.height }))

  // Hit-test subcards — clip to the union of touched ones for a tight crop.
  const subcards = Array.from(cellEl.querySelectorAll(subcardSelector)) as HTMLElement[]
  const PAD = 4
  const hits = subcards
    .map(sc => ({ sc, r: sc.getBoundingClientRect() }))
    .filter(({ r }) => {
      const lL = r.left - elRect.left, lT = r.top - elRect.top
      const lR = r.right - elRect.left, lB = r.bottom - elRect.top
      return strokeLocal.some(p =>
        p.x >= lL - PAD && p.x <= lR + PAD && p.y >= lT - PAD && p.y <= lB + PAD)
    })
  // Drop ancestors when a descendant is also touched.
  const touched = hits.filter(({ sc }) =>
    !hits.some(other => other.sc !== sc && sc.contains(other.sc)))

  let cropL: number, cropT: number, cropW: number, cropH: number
  if (touched.length > 0) {
    const ls = touched.map(t => t.r.left - elRect.left)
    const ts = touched.map(t => t.r.top - elRect.top)
    const rs = touched.map(t => t.r.right - elRect.left)
    const bs = touched.map(t => t.r.bottom - elRect.top)
    cropL = Math.max(0, Math.min(...ls) - PAD)
    cropT = Math.max(0, Math.min(...ts) - PAD)
    cropW = Math.min(elRect.width - cropL, Math.max(...rs) + PAD - cropL)
    cropH = Math.min(elRect.height - cropT, Math.max(...bs) + PAD - cropT)
  } else {
    const xs = strokeLocal.map(p => p.x), ys = strokeLocal.map(p => p.y)
    const SPAD = 20
    cropL = Math.max(0, Math.min(...xs) - SPAD)
    cropT = Math.max(0, Math.min(...ys) - SPAD)
    cropW = Math.min(elRect.width - cropL, Math.max(...xs) + SPAD - cropL)
    cropH = Math.min(elRect.height - cropT, Math.max(...ys) + SPAD - cropT)
  }

  let b64: string
  try {
    const h2c = (await import('html2canvas')).default
    // html2canvas v1.4.x renders <textarea>/<input> values as a SINGLE
    // unwrapped line clipped to the control's bbox (see its
    // `renderTextWithLetterSpacing(new TextBounds(container.value, …))`),
    // so a multi-line caption rasterizes as a sliver of the first few
    // characters at vertical center — "the minimized image looks odd"
    // when the user highlighted caption text (PK 2026-06-09). Swapping the
    // textarea for a div in the CLONED doc lets html2canvas use the normal
    // text layout (wraps + multi-line). The live DOM is untouched.
    const full = await h2c(cellEl, {
      backgroundColor: '#ffffff', scale: 1, logging: false, useCORS: true,
      onclone: (_doc, root) => {
        // (1) Hide highlight overlays in the clone so the yellow inset border
        //     + live SVG stroke don't bleed into the rasterized image — the
        //     "right half all yellow" distortion when the surface lives as
        //     a descendant of the captured cell. Cheap: a few selectors,
        //     no live-DOM change. Replaces the prior DOM-restructure fix.
        root.querySelectorAll('.rv-panel__hl, .msg__hl').forEach(n => {
          (n as HTMLElement).style.display = 'none'
        })
        // (2) Swap textareas/inputs for plain divs in the clone — see comment
        //     above for the single-line-rendering rationale.
        const liveAreas = Array.from(cellEl.querySelectorAll('textarea, input[type="text"]')) as
          (HTMLTextAreaElement | HTMLInputElement)[]
        const cloneAreas = Array.from(root.querySelectorAll('textarea, input[type="text"]')) as
          (HTMLTextAreaElement | HTMLInputElement)[]
        cloneAreas.forEach((cloneEl, i) => {
          const liveEl = liveAreas[i]
          if (!liveEl) return
          const cs = window.getComputedStyle(liveEl)
          const div = root.ownerDocument!.createElement('div')
          div.textContent = cloneEl.value || ''
          // Match the live element's visual frame closely enough that the
          // rasterized content lines up with the caption's on-screen bbox.
          // Keep position/size from the live computed style; preserve newlines
          // via white-space: pre-wrap so multi-line captions stay multi-line.
          const w = liveEl.getBoundingClientRect().width
          const h = liveEl.getBoundingClientRect().height
          div.style.cssText = (
            `box-sizing: ${cs.boxSizing};` +
            `width: ${w}px;` +
            `min-height: ${h}px;` +
            `padding: ${cs.padding};` +
            `border: ${cs.border};` +
            `border-radius: ${cs.borderRadius};` +
            `background: ${cs.backgroundColor};` +
            `font: ${cs.font};` +
            `line-height: ${cs.lineHeight};` +
            `color: ${cs.color};` +
            `white-space: pre-wrap;` +
            `word-wrap: break-word;` +
            `overflow: hidden;` +
            `text-align: ${cs.textAlign};`
          )
          cloneEl.parentNode?.replaceChild(div, cloneEl)
        })
      },
    })
    const cropC = document.createElement('canvas')
    cropC.width = Math.max(1, Math.round(cropW))
    cropC.height = Math.max(1, Math.round(cropH))
    cropC.getContext('2d')!.drawImage(full, cropL, cropT, cropW, cropH, 0, 0, cropC.width, cropC.height)
    const longest = Math.max(cropC.width, cropC.height)
    const dscale = longest > 1024 ? 1024 / longest : 1
    const W = Math.round(cropC.width * dscale), H = Math.round(cropC.height * dscale)
    const c = document.createElement('canvas'); c.width = W; c.height = H
    const ctx = c.getContext('2d')!
    ctx.drawImage(cropC, 0, 0, W, H)
    ctx.strokeStyle = HILITE; ctx.lineWidth = Math.max(10, W / 32); ctx.lineCap = 'round'; ctx.lineJoin = 'round'
    ctx.beginPath()
    strokeLocal.forEach((p, i) => {
      const cx = (p.x - cropL) * dscale, cy = (p.y - cropT) * dscale
      if (i) ctx.lineTo(cx, cy); else ctx.moveTo(cx, cy)
    })
    ctx.stroke()
    b64 = c.toDataURL('image/png').split(',')[1]
  } catch {
    return null
  }

  const shape = describeStroke(ptsNorm)
  const figCtx = describeHighlightedFigure(cellEl, entities)
  const touchedDesc = touched.map(t => describeSubcard(t.sc)).filter(Boolean).join('; ')
  const target = touchedDesc ? 'region' : (onlyImage ? 'figure' : 'message')
  const cellDesc = touchedDesc
    ? `The mark touches: ${touchedDesc}.`
    : (cellText && cellText.trim().length > 0)
      ? `The highlighted content text: "${cellText.slice(0, 500)}".`
      : (figCtx || `The marked element is an image.`)
  const note =
    `User highlight (this turn): ${shape} on the attached ${target}. ${cellDesc} ` +
    `The mark is a strong topical hint — if the question is short or demonstrative ` +
    `("what is this?", "what are these?", "this", "here"), it's about the marked region. ` +
    `If the question is clearly about the broader figure (axes, comparison to other parts, ` +
    `overall layout), answer that — the mark just points at which figure they mean.`
  return { image: b64, note }
}


/** The yellow highlighter icon — used by both ChatPane and ResultView's
 *  hl-toggle buttons. SVG ships from this module so all surfaces share
 *  the same affordance. */
export const HighlighterIcon = '__use_inline_svg__'   // placeholder, see JSX below
