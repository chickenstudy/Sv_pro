import { useState, useRef } from 'react'
import useSWR from 'swr'
import { camerasApi, type Camera } from '../api'
import { Camera as CameraIcon, Plus, X, Save, Clock, HelpCircle, Pencil, Trash2 } from 'lucide-react'

const AI_MODE_OPTIONS = [
  { value: 'both', label: 'LPR + FR' },
  { value: 'lpr', label: 'Chỉ LPR' },
  { value: 'fr', label: 'Chỉ FR' },
  { value: 'off', label: 'Tắt AI' },
]

const AI_MODE_BADGE: Record<string, string> = {
  both: 'brand', lpr: 'info', fr: 'success', off: 'low',
}

interface FormState {
  id?: number
  name: string
  rtsp_url: string
  location: string
  zone: string
  ai_mode: string
  fps_limit: number
  enabled: boolean
}

const BLANK: FormState = { name: '', rtsp_url: '', location: '', zone: '', ai_mode: 'both', fps_limit: 10, enabled: true }

export default function CamerasPage() {
  const { data: cameras = [], error, isLoading, mutate } = useSWR('/api/cameras', camerasApi.list)

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<FormState>(BLANK)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState('')
  const [deleting, setDeleting] = useState<number | null>(null)

  const handleOpenCreate = () => {
    setForm(BLANK)
    setFormError('')
    setShowForm(true)
    scrollToForm()
  }

  const handleOpenEdit = (cam: Camera) => {
    setForm({
      id: cam.id,
      name: cam.name,
      rtsp_url: cam.rtsp_url,
      location: cam.location ?? '',
      zone: cam.zone ?? '',
      ai_mode: cam.ai_mode,
      fps_limit: cam.fps_limit,
      enabled: cam.enabled,
    })
    setFormError('')
    setShowForm(true)
    scrollToForm()
  }

  const scrollToForm = () => {
    document.getElementById('camera-form')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name.trim() || !form.rtsp_url.trim()) {
      setFormError('Tên và RTSP URL là bắt buộc')
      return
    }
    setSaving(true)
    setFormError('')
    try {
      const payload = {
        name: form.name.trim(),
        rtsp_url: form.rtsp_url.trim(),
        location: form.location.trim() || null,
        zone: form.zone.trim() || null,
        ai_mode: form.ai_mode,
        fps_limit: Number(form.fps_limit) || 10,
        enabled: form.enabled,
      }
      if (form.id) {
        await camerasApi.update(form.id, payload)
      } else {
        await camerasApi.create(payload)
      }
      setShowForm(false)
      mutate()
    } catch (err: any) {
      setFormError(err.message || 'Có lỗi xảy ra')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (cam: Camera) => {
    if (!confirm(`Xóa camera "${cam.name}"? Hành động này không thể hoàn tác.`)) return
    setDeleting(cam.id)
    try {
      await camerasApi.delete(cam.id)
      mutate()
    } catch (err: any) {
      alert('Xóa thất bại: ' + (err.message || err))
    } finally {
      setDeleting(null)
    }
  }

  const toggleEnabled = async (cam: Camera) => {
    await camerasApi.update(cam.id, { enabled: !cam.enabled })
    mutate()
  }

  const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
    <div className="form-group">
      <label>{label}</label>
      {children}
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8 }}>
            <CameraIcon size={22} color="var(--brand)" /> Quản lý Camera
          </h2>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
            Thêm, sửa và cấu hình các luồng camera RTSP
          </p>
        </div>
        <button className="btn btn--primary" onClick={handleOpenCreate}>
          <Plus size={16} /> Thêm Camera
        </button>
      </div>

      {/* Add / Edit form */}
      {showForm && (
        <div className="card" id="camera-form">
          <div className="card__title">
            {form.id ? <><Pencil size={16} /> Sửa Camera</> : <><Plus size={16} /> Thêm Camera Mới</>}
          </div>
          <form onSubmit={handleSave}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <Field label="Tên camera *">
                <input className="input" required value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                  placeholder="cam_entrance_01" />
              </Field>
              <Field label="RTSP URL *">
                <input className="input" required value={form.rtsp_url}
                  onChange={e => setForm(f => ({ ...f, rtsp_url: e.target.value }))}
                  placeholder="rtsp://user:pass@192.168.1.10:554/stream1" />
              </Field>
              <Field label="Vị trí">
                <input className="input" value={form.location}
                  onChange={e => setForm(f => ({ ...f, location: e.target.value }))}
                  placeholder="Cổng chính" />
              </Field>
              <Field label="Zone">
                <input className="input" value={form.zone}
                  onChange={e => setForm(f => ({ ...f, zone: e.target.value }))}
                  placeholder="entrance" />
              </Field>
              <Field label="AI Mode">
                <select className="input" value={form.ai_mode}
                  onChange={e => setForm(f => ({ ...f, ai_mode: e.target.value }))}>
                  {AI_MODE_OPTIONS.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </Field>
              <Field label="FPS Limit">
                <input className="input" type="number" min={1} max={60} value={form.fps_limit}
                  onChange={e => setForm(f => ({ ...f, fps_limit: Number(e.target.value) }))} />
              </Field>
            </div>

            {/* Toggle enabled */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                <input type="checkbox" checked={form.enabled}
                  onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))} />
                <span>Bật camera (Enabled)</span>
              </label>
            </div>

            {formError && (
              <div style={{
                color: 'var(--danger)', fontSize: 12, padding: '8px 12px', marginTop: 12,
                background: 'var(--danger)15', borderRadius: 'var(--r-md)', border: '1px solid var(--danger)30'
              }}>
                ⚠️ {formError}
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
              <button type="submit" className="btn btn--primary" disabled={saving}>
                {saving ? <><Clock size={16} className="animate-spin" /> Đang lưu...</> : <><Save size={16} /> {form.id ? 'Lưu thay đổi' : 'Tạo Camera'}</>}
              </button>
              <button type="button" className="btn btn--ghost" onClick={() => setShowForm(false)}>
                <X size={16} /> Hủy
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Error */}
      {error && (
        <div style={{
          color: 'var(--danger)', fontSize: 12, padding: '8px 12px',
          background: 'var(--danger)15', borderRadius: 'var(--r-md)', border: '1px solid var(--danger)30'
        }}>
          ❌ {(error as any)?.message || 'Có lỗi xảy ra khi tải danh sách camera'}
        </div>
      )}

      {/* Table */}
      <div className="card" style={{ padding: 0 }}>
        <div className="table-wrap">
          {isLoading ? (
            <div className="empty-state"><Clock size={36} className="empty-state__icon" /> Đang tải danh sách camera...</div>
          ) : cameras.length === 0 ? (
            <div className="empty-state">
              <HelpCircle size={36} className="empty-state__icon" />
              Chưa có camera nào. Nhấn "Thêm Camera" để bắt đầu.
            </div>
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
                          className="btn btn--sm btn--ghost"
                          onClick={() => handleOpenEdit(cam)}
                          title="Sửa camera"
                        >
                          <Pencil size={13} />
                        </button>
                        <button
                          className="btn btn--sm"
                          style={{ color: 'var(--danger)', borderColor: 'var(--danger)30' }}
                          onClick={() => handleDelete(cam)}
                          disabled={deleting === cam.id}
                          title="Xóa camera"
                        >
                          {deleting === cam.id
                            ? <Clock size={13} className="animate-spin" />
                            : <Trash2 size={13} />
                          }
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
