import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import Storyboard from './Storyboard'
import '../notebook/record.css'
import './storyboard.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Storyboard />
  </StrictMode>,
)
