import { useState, useEffect, useRef } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Camera as CameraIcon,
  Users,
  UserX,
  Car,
  Activity,
  BellRing,
  LogOut,
  ChevronLeft,
  ChevronRight,
  Target,
  User as UserIcon,
  Shield,
  Settings as SettingsIcon,
} from 'lucide-react'
import useSWR from 'swr'
import { clearToken, authApi } from '../api'

// ── Shared Config ─────────────────────────────────────────────────────────────

const NAV_ITEMS = [
  { path: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { path: '/cameras', icon: CameraIcon, label: 'Camera' },
  { path: '/users', icon: Users, label: 'Danh tính' },
  { path: '/strangers', icon: UserX, label: 'Người lạ' },
  { path: '/vehicles', icon: Car, label: 'Phương tiện' },
  { path: '/events', icon: Activity, label: 'Sự kiện Access' },
  { path: '/alerts', icon: BellRing, label: 'Cảnh báo' },
  { path: '/settings', icon: SettingsIcon, label: 'Cài đặt' },
]

const PAGE_TITLES: Record<string, string> = {
  '/': 'Tổng quan hệ thống',
  '/cameras': 'Quản lý Camera',
  '/users': 'Danh tính + Khuôn mặt',
  '/strangers': 'Theo dõi Người lạ (Strangers)',
  '/vehicles': 'Quản lý Phương tiện (LPR)',
  '/events': 'Sự kiện Access Control',
  '/alerts': 'Lịch sử Cảnh báo',
  '/settings': 'Cài đặt hệ thống',
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
            <UserMenu onLogout={handleLogout} />
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

// ── UserMenu (topbar) ────────────────────────────────────────────────────────

function UserMenu({ onLogout }: { onLogout: () => void }) {
  const { data: me } = useSWR('/api/auth/me', authApi.me, {
    shouldRetryOnError: false,
    refreshInterval: 0,
  })
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const role = (me as any)?.role || 'user'
  const username = me?.username || '...'
  const RoleIcon = role === 'admin' ? Shield : UserIcon

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        className="btn btn--ghost"
        style={{
          padding: '4px 10px',
          display: 'flex', alignItems: 'center', gap: 6,
          fontSize: 12,
        }}
        onClick={() => setOpen(o => !o)}
      >
        <div style={{
          width: 22, height: 22, borderRadius: '50%',
          background: 'var(--brand)', color: '#fff',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 11, fontWeight: 700,
        }}>
          {(username[0] || '?').toUpperCase()}
        </div>
        <span style={{ fontWeight: 600 }}>{username}</span>
        <span className="badge badge--brand" style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 10 }}>
          <RoleIcon size={9} /> {role}
        </span>
      </button>

      {open && (
        <div
          style={{
            position: 'absolute', top: 'calc(100% + 6px)', right: 0,
            minWidth: 180,
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--r-md)',
            boxShadow: '0 8px 16px rgba(0,0,0,0.2)',
            zIndex: 100,
            overflow: 'hidden',
          }}
        >
          <div style={{
            padding: '10px 12px',
            borderBottom: '1px solid var(--border)',
          }}>
            <div style={{ fontSize: 12, fontWeight: 600 }}>{username}</div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Role: {role}</div>
          </div>
          <button
            onClick={() => { setOpen(false); onLogout() }}
            style={{
              width: '100%', padding: '8px 12px',
              background: 'none', border: 'none', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 8,
              fontSize: 12, color: 'var(--danger)',
              textAlign: 'left',
            }}
            onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg)')}
            onMouseLeave={e => (e.currentTarget.style.background = 'none')}
          >
            <LogOut size={14} /> Đăng xuất
          </button>
        </div>
      )}
    </div>
  )
}
