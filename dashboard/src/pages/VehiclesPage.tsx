import { useState, useEffect } from 'react'
import { vehiclesApi, type Vehicle } from '../api'

/**
 * Trang quản lý Xe (Blacklist) — thêm xe, toggle blacklist, xem lịch sử.
 */
export default function VehiclesPage() {
  const [vehicles, setVehicles] = useState<Vehicle[]>([])
  const [loading, setLoading]   = useState(true)
  const [blOnly, setBlOnly]     = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    plate_number: '', plate_category: '', is_blacklisted: false, blacklist_reason: '',
  })
  const [saving, setSaving] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setVehicles(await vehiclesApi.list(blOnly)) }
    catch { /* silent */ }
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [blOnly])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      await vehiclesApi.create(form)
      setShowForm(false)
      load()
    } catch { /* ignore */ }
    finally { setSaving(false) }
  }

  const handleToggle = async (v: Vehicle) => {
    if (v.is_blacklisted) {
      if (!confirm(`Xóa "${v.plate_number}" khỏi blacklist?`)) return
      await vehiclesApi.toggleBlacklist(v.plate_number, false)
    } else {
      const reason = prompt(`Lý do blacklist "${v.plate_number}":`)
      if (!reason) return
      await vehiclesApi.toggleBlacklist(v.plate_number, true, reason)
    }
    load()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="flex items-center justify-between">
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700 }}>🚗 Quản lý Phương tiện</h2>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
            Đăng ký xe và danh sách phương tiện bị chú ý
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className={`btn btn--sm ${blOnly ? 'btn--danger' : 'btn--ghost'}`}
            onClick={() => setBlOnly(v => !v)}>
            {blOnly ? '⛔ Đang lọc Blacklist' : '🔍 Lọc Blacklist'}
          </button>
          <button className="btn btn--primary" onClick={() => setShowForm(v => !v)}>
            {showForm ? '✕' : '➕ Thêm xe'}
          </button>
        </div>
      </div>

      {showForm && (
        <div className="card">
          <div className="card__title">➕ Thêm Xe Mới</div>
          <form onSubmit={handleCreate} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
            <div className="form-group">
              <label>Biển số *</label>
              <input className="input font-mono" required value={form.plate_number}
                onChange={e => setForm(f => ({ ...f, plate_number: e.target.value.toUpperCase() }))}
                placeholder="51A-12345" />
            </div>
            <div className="form-group">
              <label>Loại phương tiện</label>
              <select className="input" value={form.plate_category}
                onChange={e => setForm(f => ({ ...f, plate_category: e.target.value }))}>
                <option value="">Chọn loại</option>
                <option value="car">Ô tô</option>
                <option value="motorcycle">Xe máy</option>
                <option value="truck">Xe tải</option>
                <option value="bus">Xe buýt</option>
              </select>
            </div>
            <div style={{ display: 'flex', alignItems: 'flex-end' }}>
              <button type="submit" className="btn btn--primary" disabled={saving}>
                {saving ? '⏳...' : '💾 Lưu'}
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="card" style={{ padding: 0 }}>
        <div className="table-wrap">
          {loading ? (
            <div className="empty-state"><div className="empty-state__icon">⏳</div>Đang tải...</div>
          ) : vehicles.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state__icon">🚗</div>
              {blOnly ? 'Không có xe nào trong blacklist' : 'Chưa có xe nào được đăng ký'}
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Biển số</th>
                  <th>Loại</th>
                  <th>Trạng thái</th>
                  <th>Lý do (nếu có)</th>
                  <th>Ngày đăng ký</th>
                  <th>Hành động</th>
                </tr>
              </thead>
              <tbody>
                {vehicles.map(v => (
                  <tr key={v.id}>
                    <td className="font-mono" style={{ fontWeight: 700, fontSize: 14, letterSpacing: 1 }}>
                      {v.plate_number}
                    </td>
                    <td className="text-muted text-sm">{v.plate_category ?? '—'}</td>
                    <td>
                      {v.is_blacklisted
                        ? <span className="badge badge--critical">⛔ Blacklist</span>
                        : <span className="badge badge--success">✅ Bình thường</span>
                      }
                    </td>
                    <td style={{ maxWidth: 200 }}>
                      <div className="truncate text-muted text-sm">{v.blacklist_reason ?? '—'}</div>
                    </td>
                    <td className="text-muted text-sm">
                      {new Date(v.registered_at).toLocaleDateString('vi-VN')}
                    </td>
                    <td>
                      <button
                        className={`btn btn--sm ${v.is_blacklisted ? 'btn--ghost' : 'btn--danger'}`}
                        onClick={() => handleToggle(v)}
                      >
                        {v.is_blacklisted ? '✅ Gỡ BL' : '⛔ Blacklist'}
                      </button>
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
