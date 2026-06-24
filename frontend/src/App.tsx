import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'sileo'
import Layout from './components/Layout'
import ChatView from './views/ChatView'
import ProvidersView from './views/ProvidersView'
import IntegrationsView from './views/IntegrationsView'
import McpView from './views/McpView'
import SkillsView from './views/SkillsView'
import CalendarView from './views/CalendarView'
import SeguridadView from './views/SeguridadView'
import MemoriaView from './views/MemoriaView'
import { useActiveProvider } from './hooks/useActiveProvider'

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

// Shell: renders the Layout with the active-provider state (only for the subtle
// "connect a model" nudge in the sidebar). NO onboarding wizard / redirect —
// first run lands straight on the chat; the chat itself shows the no-model alert.
function Shell() {
  const { status, hasActive, reload } = useActiveProvider()
  // Only show the nudge when we positively know there is no active provider.
  // During loading or on a list error we stay silent — a transient failure
  // must not mislead the owner into thinking no model is connected.
  const showConnectNudge = status === 'ready' && !hasActive
  return <Layout activeProviderReload={reload} hasActiveProvider={showConnectNudge} />
}

// basename="/app" matches the shell-server mount point and Vite's base: '/app/'
export default function App() {
  return (
    <BrowserRouter basename="/app">
      <Toaster position="top-right" />
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<ChatView />} />
          <Route path="programadas" element={<CalendarView />} />
          {/* Agentes = the unified team view (cards + live floor). Office merged in. */}
          <Route path="agentes" element={
            <Suspense fallback={<OfficeFallback />}>
              <OfficeView />
            </Suspense>
          } />
          <Route path="office" element={<Navigate to="/agentes" replace />} />
          <Route path="skills" element={<SkillsView />} />
          <Route path="integraciones" element={<IntegrationsView />} />
          <Route path="mcp" element={<McpView />} />
          <Route path="proveedores" element={<ProvidersView />} />
          <Route path="seguridad" element={<SeguridadView />} />
          <Route path="memoria" element={<MemoriaView />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
