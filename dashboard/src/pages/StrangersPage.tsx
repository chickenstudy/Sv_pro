import { useState, useEffect } from 'react'
import { UserX, Video, RefreshCw, Clock, AlertTriangle, Ghost, X, User } from 'lucide-react'

interface Stranger {
  id: number
  stranger_uid: string
  first_seen: string
  last_seen: string
  camera_id: string | null
  source_id: string | null
  frame_count: number
  face_crop_path: string | null
  notes: string | null
}

async function fetchStrangers(params: {
  camera_id?: string
  limit?: number
  offset?: number
}): Promise<Stranger[]> {
  const q = new URLSearchParams()
  if (params.camera_id) q.set('camera_id', params.camera_id)
  if (params.limit) q.set('limit', String(params.limit))
  if (params.offset) q.set('offset', String(params.offset))

  const token = localStorage.getItem('svpro_token')
  const BASE = (typeof import.meta !== 'undefined' && (import.meta as any).env?.VITE_API_URL) || 'http://localhost:8000'

  const res = await fetch(`${BASE}/api/strangers?${q}`, {
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export default function StrangersPage() {
  const [strangers, setStrangers] = useState<Stranger[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(true)
  const [offset, setOffset] = useState(0)
  const [filterCamera, setFilterCamera] = useState('')
  const [selected, setSelected] = useState<Stranger | null>(null)

  const PAGE_SIZE = 24

  const loadStrangers = async (reset = false) => {
    try {
      reset ? setLoading(true) : setLoadingMore(true)
      const currentOffset = reset ? 0 : offset
      const data = await fetchStrangers({
        camera_id: filterCamera || undefined,
        limit: PAGE_SIZE,
        offset: currentOffset,
      })
      if (reset) {
        setStrangers(data)
        setOffset(PAGE_SIZE)
      } else {
        setStrangers(prev => [...prev, ...data])
        setOffset(o => o + PAGE_SIZE)
      }
      setHasMore(data.length === PAGE_SIZE)
      setError(null)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
      setLoadingMore(false)
    }
  }

  useEffect(() => {
    loadStrangers(true)
    const id = setInterval(() => loadStrangers(true), 60_000)
    return () => clearInterval(id)
  }, [filterCamera])

  const formatTime = (iso: string) => {
    try {
      return new Date(iso).toLocaleString('vi-VN', {
        day: '2-digit', month: '2-digit',
        hour: '2-digit', minute: '2-digit',
      })
    } catch { return iso }
  }

  const BASE = (typeof import.meta !== 'undefined' && (import.meta as any).env?.VITE_API_URL) || 'http://localhost:8000'
  const getFaceUrl = (s: Stranger) => {
    if (s.face_crop_path) return `${BASE}/static/${s.face_crop_path}`
    return null
  }

  return (
    <div className="strangers-page">
      <div className="page-header" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <UserX size={28} color="var(--brand)" />
        <div>
          <h2 style={{ margin: 0 }}>Gallery Người Lạ</h2>
          <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>
            Tự động làm mới mỗi 60 giây · {strangers.length} người lạ
          </span>
        </div>
      </div>

      {/* ── Bộ lọc ────────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 12, margin: '16px 0', flexWrap: 'wrap' }}>
        <input
          type="text"
          className="form-input"
          placeholder="Lọc theo Camera ID..."
          value={filterCamera}
          onChange={e => setFilterCamera(e.target.value)}
          style={{ minWidth: 200 }}
        />
        <button className="btn btn-secondary" onClick={() => loadStrangers(true)} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Làm mới
        </button>
      </div>

      {/* ── Nội dung ──────────────────────────────────────────────────────── */}
      {loading ? (
        <div className="loading-state empty-state">
          <Clock size={36} className="empty-state__icon animate-spin" />
          <p>Đang tải gallery người lạ...</p>
        </div>
      ) : error ? (
        <div className="error-banner">
          <AlertTriangle size={16} /> {error}
          <button className="btn btn-sm" onClick={() => loadStrangers(true)}>Thử lại</button>
        </div>
      ) : strangers.length === 0 ? (
        <div className="empty-state">
          <Ghost size={64} style={{ opacity: 0.25 }} />
          <p>Chưa có người lạ nào được phát hiện.</p>
          <small style={{ color: 'var(--text-muted)' }}>
            Stranger tracking sẽ tự động lưu khi AI Core phát hiện mặt không rõ danh tính.
          </small>
        </div>
      ) : (
        <>
          <div className="stranger-grid">
            {strangers.map(s => {
              const faceUrl = getFaceUrl(s)
              return (
                <div key={s.id} className="stranger-card" onClick={() => setSelected(s)}>
                  <div className="stranger-avatar">
                    {faceUrl ? (
                      <img src={faceUrl} alt={`Stranger ${s.stranger_uid}`} loading="lazy"
                        onError={e => { e.currentTarget.style.display = 'none' }} />
                    ) : (
                      <div className="stranger-no-face"><User size={24} /></div>
                    )}
                    <span className="frame-badge" title="Số lần phát hiện">{s.frame_count}×</span>
                  </div>

                  <div className="stranger-info">
                    <code className="stranger-uid">{s.stranger_uid.substring(0, 8)}...</code>
                    {s.camera_id && <span className="stranger-cam inline-flex items-center gap-1"><Video size={10} /> {s.camera_id}</span>}
                    <span className="stranger-time inline-flex items-center gap-1"><Clock size={10} /> {formatTime(s.last_seen)}</span>
                  </div>
                </div>
              )
            })}
          </div>

          {hasMore && (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <button className="btn btn-secondary" onClick={() => loadStrangers(false)} disabled={loadingMore}>
                {loadingMore ? 'Đang tải...' : 'Tải thêm'}
              </button>
            </div>
          )}
        </>
      )}

      {/* ── Modal chi tiết ─────────────────────────────────────────── */}
      {selected && (
        <div className="modal-overlay" onClick={() => setSelected(null)}>
          <div className="modal-content stranger-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3><UserX size={18} className="inline mr-2" /> Chi tiết người lạ</h3>
              <button className="modal-close" onClick={() => setSelected(null)}><X size={18} /></button>
            </div>
            <div className="modal-body">
              <div className="modal-face">
                {getFaceUrl(selected) ? (
                  <img src={getFaceUrl(selected)!} alt="Face crop"
                    style={{ width: '100%', borderRadius: 8, maxHeight: 300, objectFit: 'cover' }} />
                ) : (
                  <div style={{ padding: 40, textAlign: 'center', opacity: 0.2 }}><User size={80} /></div>
                )}
              </div>

              <table className="detail-table">
                <tbody>
                  <tr><td>ID tạm</td><td><code>{selected.stranger_uid}</code></td></tr>
                  <tr><td>Camera</td><td>{selected.camera_id || '—'}</td></tr>
                  <tr><td>Lần đầu</td><td>{formatTime(selected.first_seen)}</td></tr>
                  <tr><td>Lần cuối</td><td>{formatTime(selected.last_seen)}</td></tr>
                  <tr><td>Số frame</td><td>{selected.frame_count} lần phát hiện</td></tr>
                  {selected.notes && <tr><td>Ghi chú</td><td>{selected.notes}</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
