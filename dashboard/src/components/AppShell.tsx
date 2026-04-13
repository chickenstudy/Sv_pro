import { useState, useEffect } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Camera as CameraIcon,
  Users,
  UserX,
  Car,
  DoorOpen,
  Activity,
  BellRing,
  LogOut,
  ChevronLeft,
  ChevronRight,
  Target
} from 'lucide-react'
import { clearToken } from '../api'

// ── Shared Config ─────────────────────────────────────────────────────────────

const NAV_ITEMS = [
  { path: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { path: '/cameras', icon: CameraIcon, label: 'Camera' },
  { path: '/users', icon: Users, label: 'Danh tính' },
  { path: '/strangers', icon: UserX, label: 'Người lạ' },
  { path: '/vehicles', icon: Car, label: 'Phương tiện' },
  { path: '/doors', icon: DoorOpen, label: 'Điều khiển Cửa' },
  { path: '/events', icon: Activity, label: 'Sự kiện Access' },
  { path: '/alerts', icon: BellRing, label: 'Cảnh báo' },
]

const PAGE_TITLES: Record<string, string> = {
  '/': 'Tổng quan hệ thống',
  '/cameras': 'Quản lý Camera',
  '/users': 'Quản lý Danh tính (Users)',
  '/strangers': 'Theo dõi Người lạ (Strangers)',
  '/vehicles': 'Quản lý Phương tiện (LPR)',
  '/doors': 'Quản lý Cửa (Access Control)',
  '/events': 'Sự kiện Access Control',
  '/alerts': 'Lịch sử Cảnh báo',
}

// ── AppShell Component (Layout chính) ────────────────────────────────────────

export default function AppShell({ onLogout }: { onLogout: () => void }) {
  const [collapsed, setCollapsed] = useState(false)
  const [time, setTime] = useState(() => new Date().toLocaleTimeString('vi-VN'))
  const location = useLocation()

  useEffect(() => {
    const id = setInterval(() => setTime(new Date().toLocaleTimeString('vi-VN')), 1000)
    return () => clearInterval(id)
  }, [])

  const handleLogout = () => {
    if (confirm('Đăng xuất khỏi SV-PRO?')) {
      clearToken()
      onLogout()
    }
  }

  const currentTitle = PAGE_TITLES[location.pathname] ?? 'SV-PRO'

  return (
    <div className={`layout ${collapsed ? 'layout--collapsed' : ''}`}>
      <Sidebar
        collapsed={collapsed}
        onCollapse={() => setCollapsed(c => !c)}
        onLogout={handleLogout}
      />
      <div className="main-content">
        <header className="topbar">
          <div className="topbar__title">{currentTitle}</div>
          <div className="topbar__actions">
            <div className="topbar__time" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span className="dot-live" />
              <span>{time}</span>
            </div>
            <span style={{
              fontSize: 11, color: 'var(--text-muted)', padding: '4px 8px',
              background: 'var(--bg-elevated)', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)'
            }}>
              Live System
            </span>
          </div>
        </header>

        <main className="page-body">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

// ── Sidebar Sub-Component ────────────────────────────────────────────────────

function Sidebar({ collapsed, onCollapse, onLogout }: {
  collapsed: boolean
  onCollapse: () => void
  onLogout: () => void
}) {
  return (
    <aside className="sidebar" style={{ width: collapsed ? 64 : 240 }}>
      {/* Logo */}
      <div className="sidebar__logo">
        <div className="sidebar__logo-icon">
          <Target size={20} color="#fff" />
        </div>
        {!collapsed && (
          <div>
            <div className="sidebar__logo-text">SV-PRO</div>
            <div className="sidebar__logo-sub">Surveillance v1.0</div>
          </div>
        )}
        <button
          className="btn btn--icon btn--ghost"
          style={{ marginLeft: 'auto', padding: 4 }}
          onClick={onCollapse}
          title={collapsed ? 'Mở rộng' : 'Thu nhỏ'}
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </div>

      {/* Nav */}
      <nav className="sidebar__nav">
        {!collapsed && <div className="sidebar__section-label">Điều hướng</div>}
        {NAV_ITEMS.map(item => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
            title={collapsed ? item.label : undefined}
          >
            <item.icon size={18} className="nav-item__icon" />
            {!collapsed && <span>{item.label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="sidebar__footer">
        <button
          className="nav-item"
          onClick={onLogout}
          title={collapsed ? 'Đăng xuất' : undefined}
          style={{ width: '100%', background: 'none' }}
        >
          <LogOut size={18} className="nav-item__icon" />
          {!collapsed && <span>Đăng xuất</span>}
        </button>
      </div>
    </aside>
  )
}
