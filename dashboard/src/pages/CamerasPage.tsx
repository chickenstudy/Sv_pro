import { useState } from 'react'
import useSWR from 'swr'
import { camerasApi, type Camera } from '../api'
import { Camera as CameraIcon, Plus, X, Save, Clock, HelpCircle } from 'lucide-react'

export default function CamerasPage() {
  const { data: cameras = [], error, isLoading, mutate } = useSWR('/api/cameras', camerasApi.list)

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ name: '', rtsp_url: '', location: '', zone: '', ai_mode: 'both' })
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState('')

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      await camerasApi.create(form)
      setShowForm(false)
      setForm({ name: '', rtsp_url: '', location: '', zone: '', ai_mode: 'both' })
      mutate()
    } catch (err: any) {
      setFormError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const toggleEnabled = async (cam: Camera) => {
    await camerasApi.update(cam.id, { enabled: !cam.enabled })
    mutate()
  }

  const AI_MODE_BADGE: Record<string, string> = {
    both: 'brand', lpr: 'info', fr: 'success', off: 'low',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8 }}>
            <CameraIcon size={22} color="var(--brand)" /> Quản lý Camera
          </h2>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
            Thêm và cấu hình các luồng camera RTSP
          </p>
        </div>
        <button className="btn btn--primary" onClick={() => setShowForm(v => !v)}>
          {showForm ? <><X size={16} /> Đóng</> : <><Plus size={16} /> Thêm Camera</>}
        </button>
      </div>

      {/* Add form */}
      {showForm && (
        <div className="card">
          <div className="card__title"><Plus size={16} /> Thêm Camera Mới</div>
          <form onSubmit={handleCreate} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div className="form-group">
              <label>Tên camera *</label>
              <input className="input" required value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="cam_entrance_01" />
            </div>
            <div className="form-group">
              <label>RTSP URL *</label>
              <input className="input" required value={form.rtsp_url}
                onChange={e => setForm(f => ({ ...f, rtsp_url: e.target.value }))} placeholder="rtsp://..." />
            </div>
            <div className="form-group">
              <label>Vị trí</label>
              <input className="input" value={form.location}
                onChange={e => setForm(f => ({ ...f, location: e.target.value }))} placeholder="Cổng chính" />
            </div>
            <div className="form-group">
              <label>Zone</label>
              <input className="input" value={form.zone}
                onChange={e => setForm(f => ({ ...f, zone: e.target.value }))} placeholder="entrance" />
            </div>
            <div className="form-group">
              <label>AI Mode</label>
              <select className="input" value={form.ai_mode}
                onChange={e => setForm(f => ({ ...f, ai_mode: e.target.value }))}>
                <option value="both">LPR + FR</option>
                <option value="lpr">Chỉ LPR</option>
                <option value="fr">Chỉ FR</option>
                <option value="off">Tắt AI</option>
              </select>
            </div>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8 }}>
              <button id="save-camera-btn" type="submit" className="btn btn--primary" disabled={saving}>
                {saving ? <Clock size={16} className="animate-spin" /> : <Save size={16} />}
                {saving ? ' Đang lưu...' : ' Lưu Camera'}
              </button>
            </div>
          </form>
        </div>
      )}

      {(error || formError) && (
        <div style={{
          color: 'var(--danger)', fontSize: 12, padding: '8px 12px',
          background: 'var(--danger)15', borderRadius: 'var(--r-md)', border: '1px solid var(--danger)30'
        }}>
          ❌ {formError || (error as any)?.message || 'Có lỗi xảy ra'}
        </div>
      )}

      {/* Table */}
      <div className="card" style={{ padding: 0 }}>
        <div className="table-wrap">
          {isLoading ? (
            <div className="empty-state"><Clock size={36} className="empty-state__icon" /> Đang tải danh sách camera...</div>
          ) : cameras.length === 0 ? (
            <div className="empty-state"><HelpCircle size={36} className="empty-state__icon" /> Chưa có camera nào. Nhấn "Thêm Camera" để bắt đầu.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Trạng thái</th>
                  <th>Tên Camera</th>
                  <th>Vị trí / Zone</th>
                  <th>AI Mode</th>
                  <th>FPS Limit</th>
                  <th>Hành động</th>
                </tr>
              </thead>
              <tbody>
                {cameras.map(cam => (
                  <tr key={cam.id}>
                    <td>
                      {cam.enabled
                        ? <><span className="dot-live" /> <span style={{ fontSize: 11, marginLeft: 6 }}>LIVE</span></>
                        : <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>⏸ Tắt</span>
                      }
                    </td>
                    <td>
                      <div style={{ fontWeight: 600 }}>{cam.name}</div>
                      <div className="text-muted text-sm font-mono truncate" style={{ maxWidth: 220 }}>
                        {cam.rtsp_url}
                      </div>
                    </td>
                    <td>
                      <div>{cam.location ?? '—'}</div>
                      {cam.zone && <span className="badge badge--info" style={{ marginTop: 2 }}>{cam.zone}</span>}
                    </td>
                    <td>
                      <span className={`badge badge--${AI_MODE_BADGE[cam.ai_mode] ?? 'info'}`}>
                        {cam.ai_mode.toUpperCase()}
                      </span>
                    </td>
                    <td className="font-mono">{cam.fps_limit} fps</td>
                    <td>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button
                          className={`btn btn--sm ${cam.enabled ? 'btn--ghost' : 'btn--primary'}`}
                          onClick={() => toggleEnabled(cam)}
                        >
                          {cam.enabled ? '⏸ Tắt' : '▶ Bật'}
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
