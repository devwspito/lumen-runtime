import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import AgentsView from './views/AgentsView'
import ChatView from './views/ChatView'
import ProvidersView from './views/ProvidersView'
import IntegrationsView from './views/IntegrationsView'
import McpView from './views/McpView'
import SkillsView from './views/SkillsView'
import CalendarView from './views/CalendarView'

// Code-split OfficeView at the route boundary; it imports the canvas engine
// which is non-trivial (~10 kB gzipped) and not needed on other routes.
const OfficeView = lazy(() => import('./views/OfficeView'))

function OfficeFallback() {
  return (
    <div className="state-container" aria-busy="true">
      <p className="state-label">Cargando Office…</p>
    </div>
  )
}

// basename="/app" matches the shell-server mount point and Vite's base: '/app/'
export default function App() {
  return (
    <BrowserRouter basename="/app">
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<ChatView />} />
          <Route path="programadas" element={<CalendarView />} />
          <Route path="agentes" element={<AgentsView />} />
          <Route path="office" element={
            <Suspense fallback={<OfficeFallback />}>
              <OfficeView />
            </Suspense>
          } />
          <Route path="skills" element={<SkillsView />} />
          <Route path="integraciones" element={<IntegrationsView />} />
          <Route path="mcp" element={<McpView />} />
          <Route path="proveedores" element={<ProvidersView />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
