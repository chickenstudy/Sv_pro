import { useState, type FormEvent } from 'react'
import { authApi } from '../api'

interface Props {
  onLogin: () => void
}

/**
 * Trang đăng nhập SV-PRO Dashboard.
 * Gọi authApi.login(), lưu token vào localStorage, rồi gọi onLogin() để điều hướng về dashboard.
 */
export default function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError]       = useState('')
  const [loading, setLoading]   = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await authApi.login(username, password)
      onLogin()
    } catch (err: any) {
      setError(err.message || 'Đăng nhập thất bại')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        {/* Header */}
        <div className="login-card__header">
          <div className="login-card__logo">🎯</div>
          <div>
            <div className="login-card__title">SV-PRO</div>
            <div className="login-card__sub">Smart Surveillance System</div>
          </div>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div className="form-group">
            <label htmlFor="username">Tên đăng nhập</label>
            <input
              id="username"
              className="input"
              type="text"
              placeholder="admin"
              value={username}
              onChange={e => setUsername(e.target.value)}
              required
              autoFocus
            />
          </div>

          <div className="form-group">
            <label htmlFor="password">Mật khẩu</label>
            <input
              id="password"
              className="input"
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
            />
          </div>

          {error && (
            <div style={{
              background: 'var(--danger)15',
              border: '1px solid var(--danger)40',
              borderRadius: 'var(--r-md)',
              padding: '8px 12px',
              fontSize: 12,
              color: 'var(--danger)',
            }}>
              ⚠️ {error}
            </div>
          )}

          <button
            id="login-btn"
            type="submit"
            className="btn btn--primary"
            disabled={loading}
            style={{ marginTop: 4, width: '100%', justifyContent: 'center', padding: '10px' }}
          >
            {loading ? '⏳ Đang đăng nhập...' : '🔐 Đăng nhập'}
          </button>
        </form>

        {/* Footer hint */}
        <div style={{ textAlign: 'center', fontSize: 11, color: 'var(--text-muted)' }}>
          Phiên làm việc sẽ hết hạn sau 24 giờ
        </div>
      </div>
    </div>
  )
}
