/**
 * ConfirmDialog — small modal for confirming a destructive or blocking
 * action. Two modes (driven by props):
 *
 *   - `confirm` mode (default): primary "Continue" button + Cancel.
 *     Use for actions the user CAN proceed with after acknowledging
 *     consequences (e.g. "remove this figure from the result").
 *
 *   - `info` mode: single acknowledgement button (label customizable).
 *     Use when the user's requested action is BLOCKED with instructions
 *     for what to do instead (e.g. "to delete the Result, use the ⋯
 *     menu in the left rail").
 *
 * The variant prop (`destructive` | `warning` | `normal` | `info`)
 * controls the primary button's color so destructive actions read as
 * destructive at a glance.
 *
 * Styling lives in ConfirmDialog.css; the markup is intentionally tiny
 * (no portal — render inline; backdrop click + Escape dismiss).
 */
import { useEffect } from 'react'
import type { ReactNode } from 'react'
import './ConfirmDialog.css'


export interface ConfirmDialogProps {
  title: string
  body: ReactNode
  /** Mode: 'confirm' (primary + cancel) or 'info' (single ack button). */
  mode?: 'confirm' | 'info'
  /** Primary button label. Defaults: 'Continue' (confirm) / 'Got it' (info). */
  primaryLabel?: string
  /** Cancel button label (confirm mode only). Default: 'Cancel'. */
  cancelLabel?: string
  /** Visual treatment of the primary button. */
  variant?: 'normal' | 'destructive' | 'warning' | 'info'
  /** Called on primary button click. In 'info' mode, this also fires
   *  on Escape / backdrop click — there's no other dismissal path. */
  onPrimary: () => void
  /** Called when the user dismisses (cancel, backdrop, Escape). In
   *  'info' mode this can be the same callback as onPrimary. */
  onCancel: () => void
}


export default function ConfirmDialog({
  title, body, mode = 'confirm',
  primaryLabel, cancelLabel = 'Cancel',
  variant = 'normal',
  onPrimary, onCancel,
}: ConfirmDialogProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  const isInfo = mode === 'info'
  const label = primaryLabel ?? (isInfo ? 'Got it' : 'Continue')
  return (
    <div className="confirm-backdrop" onClick={onCancel}>
      <div className="confirm-dialog" onClick={e => e.stopPropagation()} role="dialog" aria-labelledby="confirm-dialog-title">
        <h3 id="confirm-dialog-title" className="confirm-dialog__title">{title}</h3>
        <div className="confirm-dialog__body">{body}</div>
        <div className="confirm-dialog__buttons">
          {!isInfo && (
            <button type="button" className="confirm-dialog__btn" onClick={onCancel}>
              {cancelLabel}
            </button>
          )}
          <button
            type="button"
            className={`confirm-dialog__btn confirm-dialog__btn--${variant}`}
            onClick={onPrimary}
            autoFocus
          >
            {label}
          </button>
        </div>
      </div>
    </div>
  )
}
