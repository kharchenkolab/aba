/**
 * Message-renderer registry — neutral storage between platform and bio.
 *
 * ChatPane (in src/platform/) holds the chat infrastructure (message
 * list, streaming, queue badges, error UI). The per-message rendering —
 * agent avatars, entity-pin gestures, highlight tooling — is biology
 * flavored. The shell asks `message_renderer()` for a component and
 * renders it; bio's side of the wave registers ../bio/Message here on
 * module load (see src/bio/messageRendererDefault.tsx).
 *
 * Keeping the registry under lib/ holds the platform/__platform_imports
 * lint test green — ChatPane reads from lib/ instead of importing
 * ../bio/Message directly.
 */
import type { ComponentType } from 'react'

// We intentionally use a loose component type — Message has a wide
// prop signature (annotations, clarifications, ...) and re-declaring
// it here would duplicate the platform/bio contract. The shell just
// passes its props through; if the registered component doesn't
// accept them React's normal type-checking flags it at the call site.
type Renderer = ComponentType<any>

let _renderer: Renderer | null = null

/** Register the chat-message renderer. Last registration wins. */
export function register_message_renderer(c: Renderer): void {
  _renderer = c
}

/** The currently registered renderer. Falls back to null when bio hasn't
 *  loaded yet — caller should render a tiny placeholder ("…") then. */
export function message_renderer(): Renderer | null {
  return _renderer
}
