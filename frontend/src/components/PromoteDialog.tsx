/**
 * Modal for promoting an entity up the abstraction hierarchy:
 *   figure  → result   (figure is the evidence, user types an interpretation)
 *   result  → finding  (one or more results, user types the synthesis text)
 *   finding → claim    (one or more findings, user types the claim text)
 *
 * Used from the FocusCanvas; closes on save (callback) or cancel.
 */
import { useEffect, useRef, useState } from 'react'
import './PromoteDialog.css'

interface Props {
  title: string
  placeholder: string
  prompt: string
  onCancel: () => void
  onSubmit: (text: string) => Promise<void>
  /** Optional async pre-fill (e.g. Guide's interpretation of the figure). */
  suggest?: () => Promise<string>
}

export default function PromoteDialog({ title, placeholder, prompt, onCancel, onSubmit, suggest }: Props) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [suggesting, setSuggesting] = useState(!!suggest)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    function key(e: KeyboardEvent) {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [onCancel])

  // Pre-fill with Guide's best guess (editable). Only seeds if untouched.
  // Fires ONCE on mount — `suggest` is typically defined inline at the
  // call site, so depending on it would re-fire this effect on every
  // parent re-render, leaving the dialog stuck on "Reading Guide's
  // interpretation…" while parallel fetches piled up. The function is
  // captured at mount via ref, sidestepping the dep-equality issue.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const suggestRef = useRef(suggest)
  useEffect(() => {
    const fn = suggestRef.current
    if (!fn) return
    let cancelled = false
    fn()
      .then(s => { if (!cancelled && s) setText(t => (t ? t : s)) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setSuggesting(false) })
    return () => { cancelled = true }
  }, [])

  async function submit() {
    if (!text.trim() || busy) return
    setBusy(true); setErr(null)
    try {
      await onSubmit(text.trim())
    } catch (e) {
      setErr(String(e))
      setBusy(false)
    }
  }

  return (
    <div className="promote-backdrop" onClick={onCancel}>
      <div className="promote-dialog" onClick={e => e.stopPropagation()}>
        <h3 className="promote-dialog__title">{title}</h3>
        <p className="promote-dialog__prompt">{prompt}</p>
        <textarea
          className="promote-dialog__textarea"
          placeholder={suggesting ? 'Reading Guide’s interpretation…' : placeholder}
          value={text}
          onChange={e => setText(e.target.value)}
          autoFocus
          rows={4}
          disabled={busy}
        />
        {err && <div className="promote-dialog__error">{err}</div>}
        <div className="promote-dialog__buttons">
          <button onClick={onCancel} disabled={busy} className="promote-dialog__btn">Cancel</button>
          <button
            onClick={submit}
            disabled={busy || !text.trim()}
            className="promote-dialog__btn promote-dialog__btn--primary"
          >
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
