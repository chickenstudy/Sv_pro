import { useState, useEffect, useCallback } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { isLoggedIn } from './api'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import CamerasPage from './pages/CamerasPage'
import UsersPage from './pages/UsersPage'
import VehiclesPage from './pages/VehiclesPage'
import EventsPage from './pages/EventsPage'
import AlertsPage from './pages/AlertsPage'
import StrangersPage from './pages/StrangersPage'
import SettingsPage from './pages/SettingsPage'
import LprPage from './pages/LprPage'
import BehaviorPage from './pages/BehaviorPage'
import ReportsPage from './pages/ReportsPage'
import AppShell from './components/AppShell'
import './index.css'

/**
 * Root Router — quản lý auth state và React Router v6.
 * BrowserRouter wrap toàn bộ để tránh unmount/remount router context khi login.
 */
export default function App() {
  const [loggedIn, setLoggedIn] = useState(isLoggedIn())

  const handleLogin = useCallback(() => {
    setLoggedIn(true)
  }, [])

  const handleLogout = useCallback(() => {
    setLoggedIn(false)
  }, [])

  // Lắng nghe event từ api.ts khi token hết hạn (401)
  useEffect(() => {
    const handler = () => setLoggedIn(false)
    window.addEventListener('svpro:auth-required', handler)
    return () => window.removeEventListener('svpro:auth-required', handler)
  }, [])

  return (
    <Routes>
      {!loggedIn ? (
        <Route path="*" element={<LoginPage onLogin={handleLogin} />} />
      ) : (
        <Route path="/*" element={<AppShell onLogout={handleLogout} />}>
          <Route index element={<DashboardPage />} />
          <Route path="cameras" element={<CamerasPage />} />
          <Route path="users" element={<UsersPage />} />
          {/* /enroll cũ → redirect sang /users (UsersPage có cả create + enroll + search) */}
          <Route path="enroll" element={<Navigate to="/users" replace />} />
          <Route path="vehicles" element={<VehiclesPage />} />
          <Route path="events" element={<EventsPage />} />
          <Route path="alerts" element={<AlertsPage />} />
          <Route path="strangers" element={<StrangersPage />} />
          <Route path="lpr" element={<LprPage />} />
          <Route path="behavior" element={<BehaviorPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="login" element={<Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      )}
    </Routes>
  )
}
