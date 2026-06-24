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
import ArchivosView from './views/ArchivosView'
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

// Shell: renders the Layout. The sidebar "connect a model" nudge was removed
// (redundant + the chat shows its own in-chat no-model alert). We keep
// useActiveProvider only to expose reload() so ProvidersView can refresh after
// connecting a model.
function Shell() {
  const { reload } = useActiveProvider()
  return <Layout activeProviderReload={reload} />
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
          <Route path="archivos" element={<ArchivosView />} />
          <Route path="proveedores" element={<ProvidersView />} />
          <Route path="seguridad" element={<SeguridadView />} />
          <Route path="memoria" element={<MemoriaView />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
