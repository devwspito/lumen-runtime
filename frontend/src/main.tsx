import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { startTokenRefresh } from './lib/token'
import 'sileo/styles.css'
import './styles.css'

// Keep the rotating session token alive while the tab is open (no 401 mid-use).
startTokenRefresh()

const rootEl = document.getElementById('root')
if (!rootEl) throw new Error('Root element #root not found')

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
