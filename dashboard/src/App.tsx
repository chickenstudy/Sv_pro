import { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
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
import AppShell from './components/AppShell'
import './index.css'

/**
 * Root Router — quản lý auth state và React Router v6.
 */
export default function App() {
  const [loggedIn, setLoggedIn] = useState(isLoggedIn())

  // Lắng nghe sự kiện login changes (từ api.ts hoặc các tab khác)
  useEffect(() => {
    const handleStorage = () => setLoggedIn(isLoggedIn())
    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [])

  if (!loggedIn) {
    return <LoginPage onLogin={() => setLoggedIn(true)} />
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="cameras" element={<CamerasPage />} />
          <Route path="users" element={<UsersPage />} />
          <Route path="vehicles" element={<VehiclesPage />} />
          <Route path="events" element={<EventsPage />} />
          <Route path="alerts" element={<AlertsPage />} />
          <Route path="strangers" element={<StrangersPage />} />
          <Route path="doors" element={<DoorsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
