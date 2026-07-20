/**
 * Storyboard — ten moments of a scientist working through the Record.
 *
 * Each scene mounts the SAME Record renderer over a different World (the
 * document exactly as it stands at that moment, plus what's open). The bar
 * navigates; some scenes advance through their own affordances (typing in
 * the day-0 composer, clicking `work ▸` on a question, `file & close` on a
 * session) — the story moves the way the scientist would.
 */
import { useEffect, useState } from 'react'
import Record from '../notebook/Record'
import { SCENES, GROUPS } from './scenes'

export default function Storyboard() {
  const [idx, setIdx] = useState(() => {
    const step = new URLSearchParams(window.location.search).get('step')
    const i = SCENES.findIndex(s => s.id === step)
    return i >= 0 ? i : 0
  })
  const scene = SCENES[idx]

  const goto = (i: number) => {
    const n = Math.max(0, Math.min(SCENES.length - 1, i))
    setIdx(n)
    const url = new URL(window.location.href)
    url.searchParams.set('step', SCENES[n].id)
    window.history.replaceState(null, '', url.toString())
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === 'INPUT') return
      if (e.key === 'ArrowRight') goto(idx + 1)
      if (e.key === 'ArrowLeft') goto(idx - 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const onAdvance = (trigger: string) => {
    if (scene.advance && scene.advance.on === trigger) goto(idx + 1)
  }

  return (
    <div className="story">
      <div className="story__bar">
        <div className="story__title">
          <b>The Record</b> — a scientist's workflow
          <span className="story__subtitle">the document is where you stand · sessions are where you reach</span>
        </div>
        {GROUPS.map(g => (
          <div key={g.id} className="story__group">
            <div className="story__glabel">{g.label}</div>
            <div className="story__pills">
              {SCENES.map((s, i) => s.group === g.id && (
                <button key={s.id} className={`story__pill ${i === idx ? 'is-on' : ''}`}
                        onClick={() => goto(i)} title={s.title}>
                  <span className="story__pillnum">{i + 1}</span> {s.title}
                </button>
              ))}
            </div>
          </div>
        ))}
        <div className="story__nav">
          <button onClick={() => goto(idx - 1)} disabled={idx === 0}>←</button>
          <button onClick={() => goto(idx + 1)} disabled={idx === SCENES.length - 1}>→</button>
        </div>
      </div>
      <div className="story__caption">
        <span className="story__step">{scene.id.toUpperCase()}</span>
        <span className="story__narration">{scene.narration}</span>
        {scene.advance && <span className="story__hint">▸ {scene.advance.hint}</span>}
      </div>
      <div className="story__scene">
        <Record key={scene.id} world={scene.world} onAdvance={onAdvance} />
      </div>
    </div>
  )
}
