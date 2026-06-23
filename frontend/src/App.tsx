import { lazy, Suspense, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom'
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
import OnboardingView from './views/OnboardingView'
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

// ── Onboarding gate ───────────────────────────────────────────────────────────
//
// Wraps the Layout subtree. On initial load it checks whether the user has an
// active provider. If not, it redirects to /bienvenida (once — it doesn't
// loop if the user explicitly chose "Hacerlo luego").
//
// The gate uses sessionStorage so it only fires once per tab session. If the
// user refreshes after skipping, we re-check the backend (cheap GET) and only
// redirect if still no provider.

const GATE_KEY = 'lumen_onboarding_shown'

function OnboardingGate() {
  const { status, hasActive, reload } = useActiveProvider()
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    if (status !== 'ready') return
    // Already on /bienvenida — don't loop
    if (location.pathname === '/bienvenida') return
    // Already showed the wizard this session and user skipped — respect that
    if (sessionStorage.getItem(GATE_KEY)) return

    if (!hasActive) {
      sessionStorage.setItem(GATE_KEY, '1')
      navigate('/bienvenida', { replace: true })
    }
  }, [status, hasActive, navigate, location.pathname])

  // While the API call is in-flight, do nothing (let the current route render
  // normally to avoid a flash of redirect).
  return <Layout activeProviderReload={reload} hasActiveProvider={hasActive} />
}

// basename="/app" matches the shell-server mount point and Vite's base: '/app/'
export default function App() {
  return (
    <BrowserRouter basename="/app">
      <Toaster position="top-right" />
      <Routes>
        {/* Onboarding wizard — outside the main Layout shell */}
        <Route
          path="bienvenida"
          element={<OnboardingEntry />}
        />

        {/* Main shell with the onboarding gate */}
        <Route element={<OnboardingGate />}>
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

// OnboardingEntry — full-screen wrapper that owns the provider-ready callback
function OnboardingEntry() {
  const navigate = useNavigate()
  const { reload } = useActiveProvider()

  function handleDone() {
    // Clear the gate so Layout won't re-redirect after model is connected
    sessionStorage.removeItem(GATE_KEY)
    reload()
    navigate('/chat', { replace: true })
  }

  return <OnboardingView onDone={handleDone} />
}
