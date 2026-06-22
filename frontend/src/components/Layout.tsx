import { NavLink, Outlet } from 'react-router-dom'

interface NavItem {
  to: string
  label: string
  icon: React.ReactNode
}

function ChatIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M2 3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H5l-3 3V3Z"
        stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  )
}

function TasksIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="2" y="2" width="12" height="12" rx="2" stroke="currentColor" strokeWidth="1.4" />
      <path d="M5 8l2 2 4-4" stroke="currentColor" strokeWidth="1.4"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function AgentsIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="5.5" r="2.5" stroke="currentColor" strokeWidth="1.4" />
      <path d="M2 14c0-3 2.686-4.5 6-4.5S14 11 14 14"
        stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

function OfficeIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="1" y="3" width="6" height="10" rx="1" stroke="currentColor" strokeWidth="1.4" />
      <rect x="9" y="3" width="6" height="4" rx="1" stroke="currentColor" strokeWidth="1.4" />
      <rect x="9" y="9" width="6" height="4" rx="1" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}

function SkillsIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <polygon points="8,1 10,6 15,6 11,9 13,14 8,11 3,14 5,9 1,6 6,6"
        stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  )
}

function IntegrationsIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="4" cy="8" r="2" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="12" cy="4" r="2" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="12" cy="12" r="2" stroke="currentColor" strokeWidth="1.4" />
      <path d="M6 8h2M10 5 7 7M10 11 7 9"
        stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

function McpIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M8 2v3M8 11v3M2 8h3M11 8h3"
        stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="8" cy="8" r="2.5" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}

function ProvidersIcon() {
  return (
    <svg className="nav-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <ellipse cx="8" cy="4.5" rx="5.5" ry="2" stroke="currentColor" strokeWidth="1.4" />
      <path d="M2.5 4.5v7c0 1.1 2.46 2 5.5 2s5.5-.9 5.5-2v-7"
        stroke="currentColor" strokeWidth="1.4" />
      <path d="M2.5 8c0 1.1 2.46 2 5.5 2s5.5-.9 5.5-2"
        stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}

function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <path d="M7 2v10M2 7h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

const NAV_ITEMS: NavItem[] = [
  { to: '/chat',         label: 'Chat',          icon: <ChatIcon /> },
  { to: '/programadas',  label: 'Programadas',   icon: <TasksIcon /> },
  { to: '/agentes',      label: 'Agentes',       icon: <AgentsIcon /> },
  { to: '/office',       label: 'Office',        icon: <OfficeIcon /> },
  { to: '/skills',       label: 'Skills',        icon: <SkillsIcon /> },
  { to: '/integraciones',label: 'Integraciones', icon: <IntegrationsIcon /> },
  { to: '/mcp',          label: 'MCP',           icon: <McpIcon /> },
  { to: '/proveedores',  label: 'Proveedores',   icon: <ProvidersIcon /> },
]

export default function Layout() {
  return (
    <div className="app-shell">
      <nav className="sidebar" aria-label="Navegación principal">
        {/* Wordmark */}
        <div className="sidebar-wordmark">
          <div className="sidebar-wordmark-inner">
            <div className="sidebar-mark" aria-hidden="true">L</div>
            <span className="sidebar-name">Lumen</span>
          </div>
        </div>

        {/* New chat button */}
        <NavLink
          to="/chat"
          className="sidebar-new-chat"
          aria-label="Nuevo chat"
        >
          <PlusIcon />
          Nuevo chat
        </NavLink>

        {/* Scrollable area */}
        <div className="sidebar-scroll">
          {/* Main nav */}
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
        </div>

        {/* User chip */}
        <div className="sidebar-user">
          <div className="user-avatar" aria-hidden="true">U</div>
          <span className="sidebar-user-name">Lumen</span>
        </div>
      </nav>

      <main className="main-content" id="main-content" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  )
}
