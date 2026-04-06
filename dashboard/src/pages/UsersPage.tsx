import { useState, useEffect } from 'react'
import { usersApi, type User } from '../api'

const ROLE_BADGE: Record<string, string> = {
  staff: 'success', admin: 'brand', blacklist: 'critical', guest: 'medium',
}

const ROLE_LABEL: Record<string, string> = {
  staff: '👔 Nhân viên', admin: '🛡️ Admin',
  blacklist: '⛔ Blacklist', guest: '👤 Khách',
}

/**
 * Trang quản lý Người dùng — liệt kê nhân viên & blacklist,
 * thêm người mới, đổi role, vô hiệu hóa.
 */
export default function UsersPage() {
  const [users, setUsers]     = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter]   = useState<'all' | 'staff' | 'blacklist'>('all')
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    person_id: '', name: '', role: 'staff', blacklist_reason: '',
  })
  const [saving, setSaving]   = useState(false)
  const [error, setError]     = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const params = filter === 'all' ? {} : { role: filter }
      setUsers(await usersApi.list(params))
    } catch (e: any) { setError(e.message) }
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [filter])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      await usersApi.create(form)
      setShowForm(false)
      setForm({ person_id: '', name: '', role: 'staff', blacklist_reason: '' })
      load()
    } catch (e: any) { setError(e.message) }
    finally { setSaving(false) }
  }

  const handleDeactivate = async (id: number, name: string) => {
    if (!confirm(`Vô hiệu hóa người dùng "${name}"?`)) return
    await usersApi.deactivate(id)
    load()
  }

  const handleBlacklist = async (u: User) => {
    const reason = prompt(`Nhập lý do blacklist "${u.name}":`)
    if (!reason) return
    await usersApi.update(u.id, { role: 'blacklist', blacklist_reason: reason })
    load()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700 }}>👥 Quản lý Người Dùng</h2>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
            Nhân viên, khách và danh sách chú ý
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {(['all', 'staff', 'blacklist'] as const).map(f => (
            <button key={f} className={`btn btn--sm ${filter === f ? 'btn--primary' : 'btn--ghost'}`}
              onClick={() => setFilter(f)}>
              {f === 'all' ? 'Tất cả' : f === 'staff' ? '👔 Nhân viên' : '⛔ Blacklist'}
            </button>
          ))}
          <button className="btn btn--primary" onClick={() => setShowForm(v => !v)}>
            {showForm ? '✕ Đóng' : '➕ Thêm'}
          </button>
        </div>
      </div>

      {/* Add form */}
      {showForm && (
        <div className="card">
          <div className="card__title">➕ Thêm Người Dùng</div>
          <form onSubmit={handleCreate} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
            <div className="form-group">
              <label>Person ID *</label>
              <input className="input" required value={form.person_id}
                onChange={e => setForm(f => ({ ...f, person_id: e.target.value }))}
                placeholder="EMP001" />
            </div>
            <div className="form-group">
              <label>Họ và tên *</label>
              <input className="input" required value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="Nguyễn Văn A" />
            </div>
            <div className="form-group">
              <label>Role</label>
              <select className="input" value={form.role}
                onChange={e => setForm(f => ({ ...f, role: e.target.value }))}>
                <option value="staff">Nhân viên</option>
                <option value="admin">Admin</option>
                <option value="guest">Khách</option>
                <option value="blacklist">Blacklist</option>
              </select>
            </div>
            {form.role === 'blacklist' && (
              <div className="form-group" style={{ gridColumn: '1/-1' }}>
                <label>Lý do blacklist *</label>
                <input className="input" required value={form.blacklist_reason}
                  onChange={e => setForm(f => ({ ...f, blacklist_reason: e.target.value }))}
                  placeholder="Nhập lý do..." />
              </div>
            )}
            <div style={{ display: 'flex', alignItems: 'flex-end' }}>
              <button type="submit" className="btn btn--primary" disabled={saving}>
                {saving ? '⏳...' : '💾 Lưu'}
              </button>
            </div>
          </form>
        </div>
      )}

      {error && (
        <div style={{ color: 'var(--danger)', fontSize: 12, padding: '8px 12px',
          background: 'var(--danger)15', borderRadius: 'var(--r-md)' }}>
          ❌ {error}
        </div>
      )}

      {/* Table */}
      <div className="card" style={{ padding: 0 }}>
        <div className="table-wrap">
          {loading ? (
            <div className="empty-state"><div className="empty-state__icon">⏳</div>Đang tải...</div>
          ) : users.length === 0 ? (
            <div className="empty-state"><div className="empty-state__icon">👥</div>Không có người dùng nào</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Họ tên</th>
                  <th>Person ID</th>
                  <th>Role</th>
                  <th>Khuôn mặt</th>
                  <th>Ngày tạo</th>
                  <th>Hành động</th>
                </tr>
              </thead>
              <tbody>
                {users.map(u => (
                  <tr key={u.id}>
                    <td className="font-mono text-muted">#{u.id}</td>
                    <td style={{ fontWeight: 600 }}>{u.name}</td>
                    <td className="font-mono">{u.person_id}</td>
                    <td>
                      <span className={`badge badge--${ROLE_BADGE[u.role] ?? 'info'}`}>
                        {ROLE_LABEL[u.role] ?? u.role}
                      </span>
                    </td>
                    <td>
                      {u.has_embedding
                        ? <span className="badge badge--success">✅ Đã đăng ký</span>
                        : <span className="badge badge--medium">⚠️ Chưa đăng ký</span>
                      }
                    </td>
                    <td className="text-muted text-sm">
                      {new Date(u.created_at).toLocaleDateString('vi-VN')}
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 4 }}>
                        {u.role !== 'blacklist' && (
                          <button className="btn btn--sm btn--danger" onClick={() => handleBlacklist(u)}>
                            ⛔
                          </button>
                        )}
                        <button className="btn btn--sm btn--ghost" onClick={() => handleDeactivate(u.id, u.name)}>
                          🗑️
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
