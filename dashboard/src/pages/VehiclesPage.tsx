import { useState } from 'react'
import useSWR from 'swr'
import { vehiclesApi, type Vehicle } from '../api'
import { Car, Ban, Plus, X, Search, Clock, Save, CheckCircle, CarFront } from 'lucide-react'

export default function VehiclesPage() {
  const [blOnly, setBlOnly] = useState(false)
  const { data: vehicles = [], error, isLoading, mutate } = useSWR(
    ['/api/vehicles', blOnly],
    () => vehiclesApi.list(blOnly)
  )

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    plate_number: '', plate_category: '', is_blacklisted: false, blacklist_reason: '',
  })
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState('')

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      await vehiclesApi.create(form)
      setShowForm(false)
      mutate()
    } catch (err: any) { setFormError(err.message) }
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
    mutate()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Car size={22} color="var(--brand)" /> Quản lý Phương tiện
          </h2>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
            Đăng ký xe và danh sách phương tiện bị chú ý
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className={`btn btn--sm ${blOnly ? 'btn--danger' : 'btn--ghost'}`}
            onClick={() => setBlOnly(v => !v)}>
            {blOnly ? <><Ban size={14} /> Đang lọc Blacklist</> : <><Search size={14} /> Lọc Blacklist</>}
          </button>
          <button className="btn btn--primary" onClick={() => setShowForm(v => !v)}>
            {showForm ? <X size={16} /> : <><Plus size={16} /> Thêm xe</>}
          </button>
        </div>
      </div>

      {/* Add form */}
      {showForm && (
        <div className="card">
          <div className="card__title"><Plus size={16} /> Thêm Xe Mới</div>
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
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8 }}>
              <button type="submit" className="btn btn--primary" disabled={saving}>
                {saving ? <Clock size={16} className="animate-spin" /> : <Save size={16} />}
                {saving ? ' Đang lưu...' : ' Lưu'}
              </button>
            </div>
          </form>
          {formError && <div style={{ color: 'var(--danger)', fontSize: 12, marginTop: 8 }}>❌ {formError}</div>}
        </div>
      )}

      {error && (
        <div style={{
          color: 'var(--danger)', fontSize: 12, padding: '8px 12px',
          background: 'var(--danger)15', borderRadius: 'var(--r-md)'
        }}>
          ❌ {error?.message || 'Có lỗi xảy ra'}
        </div>
      )}

      {/* Table */}
      <div className="card" style={{ padding: 0 }}>
        <div className="table-wrap">
          {isLoading ? (
            <div className="empty-state"><Clock size={36} className="empty-state__icon" /> Đang tải...</div>
          ) : vehicles.length === 0 ? (
            <div className="empty-state">
              <CarFront size={36} className="empty-state__icon" />
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
                        ? <span className="badge badge--critical"><Ban size={12} className="inline mr-1" /> Blacklist</span>
                        : <span className="badge badge--success"><CheckCircle size={12} className="inline mr-1" /> Bình thường</span>
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
                        {v.is_blacklisted ? <><CheckCircle size={14} /> Gỡ BL</> : <><Ban size={14} /> Blacklist</>}
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
