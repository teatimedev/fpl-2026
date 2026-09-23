import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import DataRoot from './DataRoot.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <DataRoot />
  </StrictMode>,
)
