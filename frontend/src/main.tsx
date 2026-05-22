import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './tokens.css'
import App from './App.tsx'
import PreviewWindow from './components/PreviewWindow'
import ErrorBoundary from './components/ErrorBoundary'

// Detached-preview mode: when opened with a #preview=… hash (a separate browser
// window popped from the run-output preview), render just the preview surface —
// not the whole app — so it has a real URL and its own lightweight controls.
const isPreview = new URLSearchParams(window.location.hash.slice(1)).has('preview')

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary label="app" fallback={() => (
      <div className="errbound errbound--app">
        <h1>Something went wrong</h1>
        <p>The app hit an unexpected error. Reloading usually fixes it; your work is saved.</p>
        <button onClick={() => window.location.reload()}>Reload</button>
      </div>
    )}>
      {isPreview ? <PreviewWindow /> : <App />}
    </ErrorBoundary>
  </StrictMode>,
)
