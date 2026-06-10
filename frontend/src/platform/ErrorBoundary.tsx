/**
 * Render-error boundary. A thrown error in any descendant is caught and shown
 * as a small fallback instead of unmounting the whole React tree (white screen).
 * Use a per-message boundary so one bad message degrades locally, and a
 * top-level one as a final safety net.
 */
import { Component, type ReactNode } from 'react'
import './ErrorBoundary.css'

interface Props {
  children: ReactNode
  /** Custom fallback; defaults to a compact inline notice with Retry. */
  fallback?: (reset: () => void, error: Error) => ReactNode
  /** Tag for the console log, to locate where it failed. */
  label?: string
}
interface State { error: Error | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State { return { error } }

  componentDidCatch(error: Error, info: unknown) {
    console.error('[ErrorBoundary]', this.props.label ?? '', error, info)
  }

  reset = () => this.setState({ error: null })

  render() {
    if (this.state.error) {
      if (this.props.fallback) return this.props.fallback(this.reset, this.state.error)
      return (
        <div className="errbound">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3L22 20H2L12 3z" /><path d="M12 10v4" /><circle cx="12" cy="17.5" r="0.6" fill="currentColor" stroke="none" />
          </svg>
          <span className="errbound__text">This couldn’t be displayed.</span>
          <button className="errbound__retry" onClick={this.reset}>Retry</button>
        </div>
      )
    }
    return this.props.children
  }
}
