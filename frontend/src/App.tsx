import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import AgentsView from './views/AgentsView'
import ComingSoonView from './views/ComingSoonView'

// basename="/app" matches the shell-server mount point and Vite's base: '/app/'
export default function App() {
  return (
    <BrowserRouter basename="/app">
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/agentes" replace />} />
          <Route path="chat" element={<ComingSoonView name="Chat" />} />
          <Route path="programadas" element={<ComingSoonView name="Programadas" />} />
          <Route path="agentes" element={<AgentsView />} />
          <Route path="office" element={<ComingSoonView name="Office" />} />
          <Route path="skills" element={<ComingSoonView name="Skills" />} />
          <Route path="integraciones" element={<ComingSoonView name="Integraciones" />} />
          <Route path="mcp" element={<ComingSoonView name="MCP" />} />
          <Route path="proveedores" element={<ComingSoonView name="Proveedores" />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
