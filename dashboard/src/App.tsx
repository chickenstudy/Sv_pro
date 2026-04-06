import { useState, useEffect } from 'react'
import { isLoggedIn } from './api'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import CamerasPage from './pages/CamerasPage'
import UsersPage from './pages/UsersPage'
import VehiclesPage from './pages/VehiclesPage'
import EventsPage from './pages/EventsPage'
import AlertsPage from './pages/AlertsPage'
import StrangersPage from './pages/StrangersPage'
import DoorsPage from './pages/DoorsPage'
import { Sidebar, Topbar } from './components/AppShell'
import './index.css'

type Page = 'dashboard' | 'cameras' | 'users' | 'vehicles' | 'events' | 'alerts' | 'strangers' | 'doors'

/**
 * Root App component — quản lý auth state và navigation.
 * Nếu chưa đăng nhập → LoginPage.
 * Nếu đã đăng nhập → AppShell + page tương ứng.
 */
export default function App() {
  const [loggedIn, setLoggedIn]   = useState(isLoggedIn)
  const [page, setPage]           = useState<Page>('dashboard')
  const [collapsed, setCollapsed] = useState(false)
  const [time, setTime]           = useState(() => new Date().toLocaleTimeString('vi-VN'))

  useEffect(() => {
    const id = setInterval(() => setTime(new Date().toLocaleTimeString('vi-VN')), 1000)
    return () => clearInterval(id)
  }, [])

  const handleLogout = () => {
    if (confirm('Đăng xuất khỏi SV-PRO?')) {
      localStorage.removeItem('svpro_token')
      setLoggedIn(false)
    }
  }

  if (!loggedIn) {
    return <LoginPage onLogin={() => setLoggedIn(true)} />
  }

  const PageComponent: Record<Page, JSX.Element> = {
    dashboard: <DashboardPage />,
    cameras:   <CamerasPage />,
    users:     <UsersPage />,
    vehicles:  <VehiclesPage />,
    events:    <EventsPage />,
    alerts:    <AlertsPage />,
    strangers: <StrangersPage />,
    doors:     <DoorsPage />,
  }

  return (
    <div className={`layout ${collapsed ? 'layout--collapsed' : ''}`}>
      <Sidebar
        page={page}
        onNavigate={p => setPage(p as Page)}
        collapsed={collapsed}
        onCollapse={() => setCollapsed(c => !c)}
        onLogout={handleLogout}
      />
      <div className="main-content">
        <Topbar page={page} time={time} />
        <main className="page-body">
          {PageComponent[page]}
        </main>
      </div>
    </div>
  )
}
