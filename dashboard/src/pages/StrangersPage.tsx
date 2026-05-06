import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  UserX, RefreshCw, Clock, AlertTriangle, Ghost, X, User, Camera as CameraIcon,
  Save, Trash2, UserPlus, Search, GitMerge, ChevronLeft, ChevronRight,
} from 'lucide-react'
import { detectImageUrl, getToken, strangersApi, type StrangerImage } from '../api'
import { FaceSearchModal } from './UsersPage'

interface Stranger {
  stranger_id:     string
  source_id:       string | null
  first_seen:      string | null
  last_seen:       string | null
  quality_frames:  number
  notes:           string | null
  cameras_seen:    string[]
  appearances:     number
  last_image_path: string | null
}

async function fetchStrangers(params: {
  source_id?: string
  limit?: number
  offset?: number
}): Promise<Stranger[]> {
  const q = new URLSearchParams()
  if (params.source_id) q.set('source_id', params.source_id)
  if (params.limit)     q.set('limit',  String(params.limit))
  if (params.offset)    q.set('offset', String(params.offset))

  const token = getToken()
  const BASE = (typeof import.meta !== 'undefined' && (import.meta as any).env?.VITE_API_URL) || ''

  const res = await fetch(`${BASE}/api/strangers?${q}`, {
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type':  'application/json',
    },
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

const PAGE_SIZE = 24

function Pagination({ page, hasNext, onPrev, onNext, total }: {
  page: number; hasNext: boolean; onPrev: () => void; onNext: () => void; total: number
}) {
  const btn: React.CSSProperties = {
    minWidth: 32, height: 30, padding: '0 8px',
    background: 'var(--bg-elevated)', border: '1px solid var(--border)',
    borderRadius: 'var(--r-sm)', color: 'var(--text-secondary)',
    fontSize: 12, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4,
  }
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '10px 0' }}>
      <button onClick={onPrev} disabled={page <= 1} style={{ ...btn, opacity: page <= 1 ? 0.4 : 1 }}>
        <ChevronLeft size={14} /> Trước
      </button>
      <span style={{ fontSize: 12, color: 'var(--text-muted)', minWidth: 80, textAlign: 'center' }}>
        Trang {page} · {total} người
      </span>
      <button onClick={onNext} disabled={!hasNext} style={{ ...btn, opacity: !hasNext ? 0.4 : 1 }}>
        Sau <ChevronRight size={14} />
      </button>
    </div>
  )
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('vi-VN', {
      day: '2-digit', month: '2-digit',
      hour: '2-digit', minute: '2-digit',
    })
  } catch { return iso }
}

export default function StrangersPage() {
  const [strangers, setStrangers]       = useState<Stranger[]>([])
  const [loading, setLoading]           = useState(true)
  const [error, setError]               = useState<string | null>(null)
  const [hasMore, setHasMore]           = useState(false)
  const [page, setPage]                 = useState(1)
  const [filterCamera, setFilterCamera] = useState('')
  const [selected, setSelected]         = useState<Stranger | null>(null)
  const [showSearch, setShowSearch]     = useState(false)

  const loadStrangers = async (p = page) => {
    try {
      setLoading(true)
      const data = await fetchStrangers({
        source_id: filterCamera || undefined,
        limit:     PAGE_SIZE + 1,
        offset:    (p - 1) * PAGE_SIZE,
      })
      setHasMore(data.length > PAGE_SIZE)
      setStrangers(data.slice(0, PAGE_SIZE))
      setError(null)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setPage(1)
  }, [filterCamera])

  useEffect(() => {
    loadStrangers(page)
    const id = setInterval(() => loadStrangers(page), 30_000)
    return () => clearInterval(id)
  }, [filterCamera, page])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <UserX size={26} color="var(--brand)" />
        <div>
          <h2 style={{ margin: 0, fontSize: 18 }}>Gallery người lạ (Re-ID)</h2>
          <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
            Tự refresh 30s · {strangers.length} stranger · cùng người được nhận diện qua nhiều camera
          </span>
        </div>
      </div>

      {/* Banner tìm bằng ảnh */}
      <div
        onClick={() => setShowSearch(true)}
        style={{
          display: 'flex', alignItems: 'center', gap: 14,
          padding: '14px 18px',
          background: 'linear-gradient(135deg, var(--brand)18, var(--brand)08)',
          border: '1px solid var(--brand)40',
          borderRadius: 'var(--r-lg)',
          cursor: 'pointer',
        }}
      >
        <div style={{
          width: 44, height: 44, borderRadius: '50%',
          background: 'var(--brand)25',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexShrink: 0,
        }}>
          <Search size={20} color="var(--brand)" />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: 'var(--brand)' }}>
            Tìm người lạ bằng ảnh
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            Upload hoặc chụp ảnh → so khớp với gallery người lạ + danh tính đã đăng ký
          </div>
        </div>
        <button className="btn btn--primary btn--sm" type="button">Mở tìm kiếm →</button>
      </div>

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <input
          type="text"
          className="input"
          placeholder="Lọc theo camera (vd: cam_online_1)..."
          value={filterCamera}
          onChange={e => setFilterCamera(e.target.value)}
          style={{ minWidth: 240, flex: 1 }}
        />
        <button className="btn btn--ghost" onClick={() => loadStrangers(page)} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Làm mới
        </button>
        <button
          className="btn btn--ghost"
          title="Gộp các stranger trùng visual về 1 ID đại diện (cosine sim ≥ 0.55)"
          onClick={async () => {
            const dry = await strangersApi.dedup(false)
            if (dry.clusters_found === 0) {
              alert('DB sạch — không có stranger nào trùng nhau (sim ≥ 0.55)')
              return
            }
            const ok = confirm(
              `Tìm thấy ${dry.clusters_found} cụm trùng → sẽ xóa ${dry.strangers_removed} stranger và gộp logs về ID đại diện.\n\nTiếp tục?`,
            )
            if (!ok) return
            const r = await strangersApi.dedup(true)
            alert(`Đã gộp ${r.clusters_found} cụm — xóa ${r.strangers_removed} stranger.`)
            setPage(1); loadStrangers(1)
          }}
        >
          <GitMerge size={14} /> Gộp trùng
        </button>
      </div>

      {/* Content */}
      {loading && strangers.length === 0 ? (
        <div className="empty-state">
          <Clock size={36} className="empty-state__icon animate-spin" />
          Đang tải...
        </div>
      ) : error ? (
        <div className="empty-state" style={{ color: 'var(--danger)' }}>
          <AlertTriangle size={36} className="empty-state__icon" />
          {error}
          <button className="btn btn--sm" onClick={() => loadStrangers(page)} style={{ marginTop: 8 }}>
            Thử lại
          </button>
        </div>
      ) : strangers.length === 0 ? (
        <div className="empty-state">
          <Ghost size={36} className="empty-state__icon" style={{ opacity: 0.4 }} />
          Chưa có người lạ nào.
          <small style={{ color: 'var(--text-muted)', display: 'block', marginTop: 4 }}>
            FR pipeline sẽ tự lưu khi phát hiện mặt không rõ danh tính.
          </small>
        </div>
      ) : (
        <>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
              gap: 12,
            }}
          >
            {strangers.map(s => (
              <StrangerCard
                key={s.stranger_id}
                stranger={s}
                onClick={() => setSelected(s)}
              />
            ))}
          </div>

          <Pagination
            page={page}
            hasNext={hasMore}
            total={strangers.length + (page > 1 ? (page - 1) * PAGE_SIZE : 0)}
            onPrev={() => setPage(p => Math.max(1, p - 1))}
            onNext={() => setPage(p => p + 1)}
          />
        </>
      )}

      {/* Detail modal */}
      {selected && (
        <StrangerDetailModal
          stranger={selected}
          onClose={() => setSelected(null)}
          onChanged={() => { setSelected(null); loadStrangers(page) }}
        />
      )}

      {/* Face search modal */}
      {showSearch && <FaceSearchModal onClose={() => setShowSearch(false)} />}
    </div>
  )
}

// ── Stranger Card ─────────────────────────────────────────────────────────────

function StrangerCard({ stranger, onClick }: { stranger: Stranger; onClick: () => void }) {
  const imgUrl = detectImageUrl(stranger.last_image_path)
  return (
    <div
      className="card"
      onClick={onClick}
      style={{
        padding: 0,
        cursor: 'pointer',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div
        style={{
          aspectRatio: '1 / 1',
          background: '#000',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          position: 'relative',
        }}
      >
        {imgUrl ? (
          <img
            src={imgUrl}
            alt={stranger.stranger_id}
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        ) : (
          <User size={40} style={{ color: 'var(--text-muted)' }} />
        )}
        <div
          style={{
            position: 'absolute', top: 6, right: 6,
            background: 'rgba(0,0,0,0.65)',
            color: '#fff',
            padding: '2px 6px',
            borderRadius: 'var(--r-sm)',
            fontSize: 10,
            fontWeight: 600,
          }}
          title="Số lần xuất hiện"
        >
          {stranger.appearances}×
        </div>
      </div>
      <div style={{ padding: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
        <code style={{ fontSize: 11, fontWeight: 600 }}>{stranger.stranger_id}</code>
        {/* Camera badges */}
        <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
          {(stranger.cameras_seen.length > 0
            ? stranger.cameras_seen
            : stranger.source_id ? [stranger.source_id] : []
          ).slice(0, 3).map(cam => (
            <span
              key={cam}
              className="badge badge--brand"
              style={{ fontSize: 9, display: 'inline-flex', alignItems: 'center', gap: 2 }}
              title={cam}
            >
              <CameraIcon size={9} /> {cam.length > 14 ? cam.slice(0, 12) + '…' : cam}
            </span>
          ))}
          {stranger.cameras_seen.length > 3 && (
            <span className="badge badge--low" style={{ fontSize: 9 }}>
              +{stranger.cameras_seen.length - 3}
            </span>
          )}
        </div>
        <span style={{ fontSize: 10, color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', gap: 3 }}>
          <Clock size={9} /> {fmtTime(stranger.last_seen)}
        </span>
      </div>
    </div>
  )
}

// ── Detail Modal ──────────────────────────────────────────────────────────────

function StrangerDetailModal({
  stranger, onClose, onChanged,
}: {
  stranger: Stranger
  onClose: () => void
  onChanged: () => void
}) {
  const imgUrl = detectImageUrl(stranger.last_image_path)
  const navigate = useNavigate()
  const [notes, setNotes] = useState(stranger.notes ?? '')
  const [busy, setBusy] = useState(false)
  const [err, setErr]   = useState('')
  const [images, setImages] = useState<StrangerImage[]>([])
  const [imgsLoading, setImgsLoading] = useState(false)
  const [lightbox, setLightbox] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setImgsLoading(true)
    strangersApi.listImages(stranger.stranger_id, 100)
      .then(data => { if (!cancelled) setImages(data) })
      .catch(() => { if (!cancelled) setImages([]) })
      .finally(() => { if (!cancelled) setImgsLoading(false) })
    return () => { cancelled = true }
  }, [stranger.stranger_id])

  const saveNotes = async () => {
    setBusy(true); setErr('')
    try {
      await strangersApi.addNotes(stranger.stranger_id, notes)
      onChanged()
    } catch (e: any) {
      setErr(e?.message || 'Lỗi lưu note')
    } finally {
      setBusy(false)
    }
  }

  const removeStranger = async () => {
    if (!confirm(`Xoá stranger ${stranger.stranger_id}? (Không thể hoàn tác)`)) return
    setBusy(true); setErr('')
    try {
      await strangersApi.remove(stranger.stranger_id)
      onChanged()
    } catch (e: any) {
      setErr(e?.message || 'Lỗi xoá')
    } finally {
      setBusy(false)
    }
  }

  const promoteToUser = () => {
    // Mở trang Enroll với context — operator chọn (hoặc tạo) user rồi enroll
    // bằng ảnh stranger này (sẽ lấy từ /api/detect-images).
    navigate(`/enroll?stranger_id=${encodeURIComponent(stranger.stranger_id)}`)
  }

  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1000, padding: 16,
      }}
      onClick={onClose}
    >
      <div
        className="card"
        style={{ width: 720, maxWidth: '95vw', maxHeight: '92vh', overflow: 'auto' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="card__title" style={{ justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <UserX size={16} /> Chi tiết người lạ
          </div>
          <button className="btn btn--ghost btn--sm" onClick={onClose}>
            <X size={14} />
          </button>
        </div>

        <div style={{ display: 'flex', gap: 16 }}>
          <div
            style={{
              width: 160, height: 160,
              background: '#000', borderRadius: 'var(--r-md)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              overflow: 'hidden', flexShrink: 0,
            }}
          >
            {imgUrl ? (
              <img
                src={imgUrl}
                alt={stranger.stranger_id}
                style={{ width: '100%', height: '100%', objectFit: 'cover' }}
              />
            ) : (
              <User size={64} style={{ color: 'var(--text-muted)' }} />
            )}
          </div>

          <table style={{ flex: 1, fontSize: 12 }}>
            <tbody>
              <tr>
                <td style={{ color: 'var(--text-muted)', padding: '4px 8px 4px 0' }}>Stranger ID</td>
                <td><code>{stranger.stranger_id}</code></td>
              </tr>
              <tr>
                <td style={{ color: 'var(--text-muted)', padding: '4px 8px 4px 0' }}>Lần xuất hiện</td>
                <td><strong>{stranger.appearances}</strong></td>
              </tr>
              <tr>
                <td style={{ color: 'var(--text-muted)', padding: '4px 8px 4px 0' }}>Quality frames</td>
                <td>{stranger.quality_frames}</td>
              </tr>
              <tr>
                <td style={{ color: 'var(--text-muted)', padding: '4px 8px 4px 0' }}>Lần đầu thấy</td>
                <td>{fmtTime(stranger.first_seen)}</td>
              </tr>
              <tr>
                <td style={{ color: 'var(--text-muted)', padding: '4px 8px 4px 0' }}>Lần cuối</td>
                <td>{fmtTime(stranger.last_seen)}</td>
              </tr>
              <tr>
                <td style={{ color: 'var(--text-muted)', padding: '4px 8px 4px 0', verticalAlign: 'top' }}>Camera đã thấy</td>
                <td>
                  {stranger.cameras_seen.length === 0 ? (
                    stranger.source_id ?? '—'
                  ) : (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {stranger.cameras_seen.map(c => (
                        <span key={c} className="badge badge--brand" style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                          <CameraIcon size={10} /> {c}
                        </span>
                      ))}
                    </div>
                  )}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        {/* Notes editor */}
        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <label style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>
            Ghi chú của operator
          </label>
          <textarea
            className="input"
            rows={3}
            value={notes}
            onChange={e => setNotes(e.target.value)}
            placeholder="Ví dụ: Người này thấy giống bạn của X..."
            style={{ resize: 'vertical', fontSize: 12 }}
          />
        </div>

        {err && (
          <div style={{
            color: 'var(--danger)', fontSize: 12, padding: '8px 12px',
            background: 'var(--danger)15', borderRadius: 'var(--r-md)', marginTop: 8,
          }}>
            ❌ {err}
          </div>
        )}

        {/* Actions */}
        <div style={{
          display: 'flex', gap: 6, marginTop: 12, flexWrap: 'wrap',
        }}>
          <button className="btn btn--primary btn--sm" onClick={saveNotes} disabled={busy || notes === (stranger.notes ?? '')}>
            <Save size={12} /> Lưu ghi chú
          </button>
          <button className="btn btn--ghost btn--sm" onClick={promoteToUser} disabled={busy}
                  title="Mở trang Đăng ký để biến stranger này thành user chính thức">
            <UserPlus size={12} /> Promote → User
          </button>
          <button className="btn btn--danger btn--sm" onClick={removeStranger} disabled={busy} style={{ marginLeft: 'auto' }}>
            <Trash2 size={12} /> Xoá stranger
          </button>
        </div>

        {/* ── Gallery: tất cả ảnh đã chụp của stranger ──────────────────── */}
        <div style={{
          marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--border)',
        }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            fontSize: 12, fontWeight: 700, marginBottom: 8,
            color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5,
          }}>
            <CameraIcon size={13} />
            <span>Ảnh đã ghi nhận</span>
            <span style={{
              fontSize: 10, background: 'var(--brand)20', color: 'var(--brand)',
              padding: '2px 8px', borderRadius: 10,
            }}>{images.length}</span>
          </div>

          {imgsLoading ? (
            <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: 8 }}>
              <Clock size={12} className="animate-spin" /> Đang tải ảnh…
            </div>
          ) : images.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: 8 }}>
              Chưa có ảnh nào trong recognition_logs.
            </div>
          ) : (
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
              gap: 8,
            }}>
              {images.map((img, i) => {
                const url = detectImageUrl(img.image_path)
                return (
                  <div
                    key={i}
                    onClick={() => url && setLightbox(url)}
                    style={{
                      background: '#000', borderRadius: 'var(--r-md)',
                      overflow: 'hidden', cursor: url ? 'pointer' : 'default',
                      aspectRatio: '1 / 1', position: 'relative',
                      border: '1px solid var(--border)',
                    }}
                    title={`${img.source_id || '—'} · ${fmtTime(img.created_at)}`}
                  >
                    {url ? (
                      <img src={url} alt=""
                           style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                           onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
                    ) : (
                      <div style={{
                        position: 'absolute', inset: 0, display: 'flex',
                        alignItems: 'center', justifyContent: 'center',
                        color: 'var(--text-muted)',
                      }}>
                        <User size={24} />
                      </div>
                    )}
                    <div style={{
                      position: 'absolute', bottom: 0, left: 0, right: 0,
                      background: 'linear-gradient(to top, rgba(0,0,0,0.85), transparent)',
                      color: '#fff', fontSize: 9,
                      padding: '10px 4px 3px',
                      display: 'flex', flexDirection: 'column', gap: 1,
                    }}>
                      <span style={{ fontWeight: 600 }}>
                        {img.source_id || '—'}
                      </span>
                      <span style={{ opacity: 0.8 }}>
                        {fmtTime(img.created_at)}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Lightbox phóng to ảnh khi click */}
      {lightbox && (
        <div
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.9)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 1100, cursor: 'zoom-out',
          }}
          onClick={e => { e.stopPropagation(); setLightbox(null) }}
        >
          <img src={lightbox} alt=""
               style={{
                 maxWidth: '95vw', maxHeight: '92vh',
                 objectFit: 'contain',
                 boxShadow: '0 0 40px rgba(0,0,0,0.8)',
               }} />
        </div>
      )}
    </div>
  )
}
