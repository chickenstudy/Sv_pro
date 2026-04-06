import { useState } from 'react'
import { clearToken } from '../api'

interface Props {
  page: string
  onNavigate: (page: string) => void
}

const NAV_ITEMS = [
  { id: 'dashboard', icon: '📊', label: 'Dashboard' },
  { id: 'cameras',   icon: '📷', label: 'Camera' },
  { id: 'users',     icon: '👥', label: 'Danh tính' },
  { id: 'strangers', icon: '👤', label: 'Người lạ' },
  { id: 'vehicles',  icon: '🚗', label: 'Phương tiện' },
  { id: 'doors',     icon: '🚪', label: 'Điều khiển Cửa' },
  { id: 'events',    icon: '🚨', label: 'Sự kiện Access' },
  { id: 'alerts',    icon: '🔔', label: 'Cảnh báo' },
]

const PAGE_TITLES: Record<string, string> = {
  dashboard: 'Tổng quan hệ thống',
  cameras:   'Quản lý Camera',
  users:     'Quản lý Danh tính (Users)',
  strangers: 'Theo dõi Người lạ (Strangers)',
  vehicles:  'Quản lý Phương tiện (LPR)',
  doors:     'Quản lý Cửa (Access Control)',
  events:    'Sự kiện Access Control',
  alerts:    'Lịch sử Cảnh báo',
}

/**
 * Shell layout: Sidebar + Topbar bao bọc toàn bộ Dashboard.
 * Quản lý state navigation, realtime clock và logout.
 */
export default function AppShell({ page, onNavigate }: Props) {
  const [collapsed, setCollapsed] = useState(false)
  const [time, setTime] = useState(() => new Date().toLocaleTimeString('vi-VN'))

  // Cập nhật đồng hồ mỗi giây
  useState(() => {
    const id = setInterval(() => setTime(new Date().toLocaleTimeString('vi-VN')), 1000)
    return () => clearInterval(id)
  })

  const handleLogout = () => {
    if (confirm('Đăng xuất khỏi SV-PRO?')) {
      clearToken()
      window.location.reload()
    }
  }

  return { collapsed, time, handleLogout }
}

/**
 * Sidebar component: navigation + logo + collapse button + logout.
 */
export function Sidebar({ page, onNavigate, collapsed, onCollapse, onLogout }: {
  page: string
  onNavigate: (p: string) => void
  collapsed: boolean
  onCollapse: () => void
  onLogout: () => void
}) {
  return (
    <aside className={`sidebar ${collapsed ? 'layout--sidebar-collapsed' : ''}`}
      style={{ width: collapsed ? 64 : 240, transition: 'width .25s ease' }}>

      {/* Logo */}
      <div className="sidebar__logo">
        <div className="sidebar__logo-icon">🎯</div>
        {!collapsed && (
          <div>
            <div className="sidebar__logo-text">SV-PRO</div>
            <div className="sidebar__logo-sub">Surveillance v1.0</div>
          </div>
        )}
        <button
          className="btn btn--icon btn--ghost"
          style={{ marginLeft: 'auto', fontSize: 12 }}
          onClick={onCollapse}
          title={collapsed ? 'Mở rộng' : 'Thu nhỏ'}
        >
          {collapsed ? '»' : '«'}
        </button>
      </div>

      {/* Nav */}
      <nav className="sidebar__nav">
        {!collapsed && <div className="sidebar__section-label">Điều hướng</div>}
        {NAV_ITEMS.map(item => (
          <button
            key={item.id}
            id={`nav-${item.id}`}
            className={`nav-item ${page === item.id ? 'nav-item--active' : ''}`}
            onClick={() => onNavigate(item.id)}
            title={collapsed ? item.label : undefined}
            style={{ width: '100%', background: 'none', border: page === item.id ? '1px solid var(--brand)30' : '1px solid transparent', cursor: 'pointer' }}
          >
            <span className="nav-item__icon">{item.icon}</span>
            {!collapsed && <span>{item.label}</span>}
          </button>
        ))}
      </nav>

      {/* Footer */}
      <div className="sidebar__footer">
        <button
          className="nav-item"
          onClick={onLogout}
          style={{ width: '100%', background: 'none', border: '1px solid transparent', cursor: 'pointer' }}
          title={collapsed ? 'Đăng xuất' : undefined}
        >
          <span className="nav-item__icon">🚪</span>
          {!collapsed && <span>Đăng xuất</span>}
        </button>
      </div>
    </aside>
  )
}

/**
 * Topbar component: tiêu đề trang + đồng hồ thực + nút refresh.
 */
export function Topbar({ page, time }: { page: string; time: string }) {
  return (
    <header className="topbar">
      <div className="topbar__title">{PAGE_TITLES[page] ?? page}</div>
      <div className="topbar__actions">
        <div className="topbar__time">🕐 {time}</div>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', padding: '4px 8px',
          background: 'var(--bg-elevated)', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)' }}>
          <span className="dot-live" style={{ marginRight: 4 }} />
          Live
        </span>
      </div>
    </header>
  )
}
