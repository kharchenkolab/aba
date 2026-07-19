import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import Record from './Record'
import './record.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Record />
  </StrictMode>,
)
