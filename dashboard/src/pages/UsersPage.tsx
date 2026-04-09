import { useState } from 'react'
import useSWR from 'swr'
import { usersApi, type User } from '../api'
import {
  Users, Shield, UserX, User as UserIcon, Plus, X, Clock, Save,
  AlertTriangle, CheckCircle, Trash2, Ban
} from 'lucide-react'

const ROLE_BADGE: Record<string, string> = {
  staff: 'success', admin: 'brand', blacklist: 'critical', guest: 'medium',
}

export default function UsersPage() {
  const [filter, setFilter] = useState<'all' | 'staff' | 'blacklist'>('all')
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    person_id: '', name: '', role: 'staff', blacklist_reason: '',
  })
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState('')

  const getRoleIcon = (role: string) => {
    switch (role) {
      case 'staff': return <UserIcon size={14} className="inline mr-1" />
      case 'admin': return <Shield size={14} className="inline mr-1" />
      case 'blacklist': return <Ban size={14} className="inline mr-1" />
      default: return <UserIcon size={14} className="inline mr-1" />
    }
  }

  const { data: users = [], error, isLoading, mutate } = useSWR(
    ['/api/users', filter],
    () => usersApi.list(filter === 'all' ? {} : { role: filter })
  )

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      await usersApi.create(form)
      setShowForm(false)
      setForm({ person_id: '', name: '', role: 'staff', blacklist_reason: '' })
      mutate()
    } catch (e: any) { setFormError(e.message) }
    finally { setSaving(false) }
  }

  const handleDeactivate = async (id: number, name: string) => {
    if (!confirm(`Vô hiệu hóa người dùng "${name}"?`)) return
    await usersApi.deactivate(id)
    mutate()
  }

  const handleBlacklist = async (u: User) => {
    const reason = prompt(`Nhập lý do blacklist "${u.name}":`)
    if (!reason) return
    await usersApi.update(u.id, { role: 'blacklist', blacklist_reason: reason })
    mutate()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Users size={22} color="var(--brand)" /> Quản lý Người Dùng
          </h2>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
            Nhân viên, khách và danh sách chú ý
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {(['all', 'staff', 'blacklist'] as const).map(f => (
            <button key={f} className={`btn btn--sm ${filter === f ? 'btn--primary' : 'btn--ghost'}`}
              onClick={() => setFilter(f)}>
              {f === 'all' ? 'Tất cả' : f === 'staff' ? 'Nhân viên' : 'Blacklist'}
            </button>
          ))}
          <button className="btn btn--primary" onClick={() => setShowForm(v => !v)}>
            {showForm ? <><X size={16} /> Đóng</> : <><Plus size={16} /> Thêm</>}
          </button>
        </div>
      </div>

      {/* Add form */}
      {showForm && (
        <div className="card">
          <div className="card__title"><Plus size={16} /> Thêm Người Dùng</div>
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
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8 }}>
              <button type="submit" className="btn btn--primary" disabled={saving}>
                {saving ? <Clock size={16} className="animate-spin" /> : <Save size={16} />}
                {saving ? ' Đang lưu...' : ' Lưu'}
              </button>
            </div>
          </form>
        </div>
      )}

      {(error || formError) && (
        <div style={{
          color: 'var(--danger)', fontSize: 12, padding: '8px 12px',
          background: 'var(--danger)15', borderRadius: 'var(--r-md)'
        }}>
          ❌ {formError || error?.message || 'Có lỗi xảy ra'}
        </div>
      )}

      {/* Table */}
      <div className="card" style={{ padding: 0 }}>
        <div className="table-wrap">
          {isLoading ? (
            <div className="empty-state"><Clock size={36} className="empty-state__icon" /> Đang tải...</div>
          ) : users.length === 0 ? (
            <div className="empty-state"><UserX size={36} className="empty-state__icon" /> Không có người dùng nào</div>
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
                        {getRoleIcon(u.role)} {u.role.toUpperCase()}
                      </span>
                    </td>
                    <td>
                      {u.has_embedding
                        ? <span className="badge badge--success"><CheckCircle size={12} className="inline mr-1" /> Đã đăng ký</span>
                        : <span className="badge badge--medium"><AlertTriangle size={12} className="inline mr-1" /> Chưa đăng ký</span>
                      }
                    </td>
                    <td className="text-muted text-sm">
                      {new Date(u.created_at).toLocaleDateString('vi-VN')}
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 4 }}>
                        {u.role !== 'blacklist' && (
                          <button className="btn btn--sm btn--danger" onClick={() => handleBlacklist(u)} title="Thêm vào Blacklist">
                            <Ban size={14} />
                          </button>
                        )}
                        <button className="btn btn--sm btn--ghost" onClick={() => handleDeactivate(u.id, u.name)} title="Xóa người dùng">
                          <Trash2 size={14} />
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
