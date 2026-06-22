import { NavLink, Outlet } from 'react-router-dom'

interface NavItem {
  to: string
  label: string
  // Inline SVG paths keep the bundle self-contained with no icon library dep.
  icon: React.ReactNode
}

function AgentsIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <circle cx="9" cy="6" r="3" stroke="currentColor" strokeWidth="1.5" />
      <path d="M3 15c0-3.314 2.686-5 6-5s6 1.686 6 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

function ChatIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <path d="M3 4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H6l-3 3V4Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}

function TasksIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <rect x="3" y="3" width="12" height="12" rx="2" stroke="currentColor" strokeWidth="1.5" />
      <path d="M6 9l2 2 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function OfficeIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <rect x="2" y="4" width="6" height="10" rx="1" stroke="currentColor" strokeWidth="1.5" />
      <rect x="10" y="4" width="6" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
      <rect x="10" y="10" width="6" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  )
}

function SkillsIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <polygon points="9,2 11,7 17,7 12.5,10.5 14.5,16 9,12.5 3.5,16 5.5,10.5 1,7 7,7" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}

function IntegrationsIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <circle cx="5" cy="9" r="2" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="13" cy="5" r="2" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="13" cy="13" r="2" stroke="currentColor" strokeWidth="1.5" />
      <path d="M7 9h2M11 5.5 8 8M11 12.5 8 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

function McpIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <path d="M9 2v4M9 12v4M2 9h4M12 9h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="9" cy="9" r="2.5" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  )
}

function ProvidersIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <ellipse cx="9" cy="5" rx="6" ry="2.5" stroke="currentColor" strokeWidth="1.5" />
      <path d="M3 5v8c0 1.38 2.686 2.5 6 2.5S15 14.38 15 13V5" stroke="currentColor" strokeWidth="1.5" />
      <path d="M3 9c0 1.38 2.686 2.5 6 2.5S15 10.38 15 9" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  )
}

const NAV_ITEMS: NavItem[] = [
  { to: '/chat', label: 'Chat', icon: <ChatIcon /> },
  { to: '/programadas', label: 'Programadas', icon: <TasksIcon /> },
  { to: '/agentes', label: 'Agentes', icon: <AgentsIcon /> },
  { to: '/office', label: 'Office', icon: <OfficeIcon /> },
  { to: '/skills', label: 'Skills', icon: <SkillsIcon /> },
  { to: '/integraciones', label: 'Integraciones', icon: <IntegrationsIcon /> },
  { to: '/mcp', label: 'MCP', icon: <McpIcon /> },
  { to: '/proveedores', label: 'Proveedores', icon: <ProvidersIcon /> },
]

export default function Layout() {
  return (
    <div className="app-shell">
      <nav className="sidebar" aria-label="Navegación principal">
        <div className="sidebar-wordmark" aria-label="Lumen">
          <div className="sidebar-mark" aria-hidden="true">L</div>
          <span className="sidebar-name">Lumen</span>
        </div>

        <div className="sidebar-nav">
          <ul role="list">
            {NAV_ITEMS.map(({ to, label, icon }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  className={({ isActive }) =>
                    ['nav-link', isActive ? 'active' : ''].filter(Boolean).join(' ')
                  }
                  aria-current={undefined}
                >
                  {icon}
                  {label}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      </nav>

      <main className="main-content" id="main-content" aria-label="Vista principal">
        <Outlet />
      </main>
    </div>
  )
}
