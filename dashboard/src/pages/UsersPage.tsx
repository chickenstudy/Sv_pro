/**
 * UsersPage — quản lý danh tính + đăng ký khuôn mặt + tìm kiếm theo ảnh.
 *
 * Modal "Thêm User" có 2 tab:
 *   - Thông tin: person_id, name, role
 *   - Khuôn mặt (optional): webcam capture / file upload (multi-shot)
 * Khi Save → tạo user → nếu có ảnh → auto enroll luôn.
 *
 * Action trên row: Cập nhật khuôn mặt (mở modal Khuôn mặt cho user đó),
 * Blacklist toggle, Xoá embedding, Xoá user.
 *
 * Có nút "Tìm bằng ảnh" → modal upload/webcam → POST /api/face-search → list match.
 */

import { useState, useRef, useEffect, useCallback, type ReactNode } from 'react'
import useSWR from 'swr'
import {
  usersApi, enrollApi, faceSearchApi, strangersApi, detectImageUrl,
  type User, type FaceMatch, type StrangerImage,
} from '../api'
import {
  Users, Shield, UserX, User as UserIcon, Plus, X, Clock, Save,
  AlertTriangle, CheckCircle, Trash2, Ban, Camera as CameraIcon, Upload,
  RefreshCw, Search, Video,
} from 'lucide-react'

const ROLE_BADGE: Record<string, string> = {
  staff: 'success', admin: 'brand', blacklist: 'critical', guest: 'medium',
}

// ── Webcam capture component (reuse từ EnrollPage cũ) ────────────────────────

interface WebcamProps {
  active: boolean
  onCapture: (file: File) => void
  onError: (msg: string) => void
}

function WebcamCapture({ active, onCapture, onError }: WebcamProps) {
  const videoRef  = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [ready, setReady] = useState(false)

  const stopStream = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop())
      streamRef.current = null
    }
    if (videoRef.current) videoRef.current.srcObject = null
    setReady(false)
  }, [])

  useEffect(() => {
    if (!active) { stopStream(); return }
    let cancelled = false
    ;(async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' },
          audio: false,
        })
        if (cancelled) { stream.getTracks().forEach(t => t.stop()); return }
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          await videoRef.current.play().catch(() => {})
          setReady(true)
        }
      } catch (e: any) {
        const msg = e?.name === 'NotAllowedError'
          ? 'Trình duyệt từ chối quyền webcam.'
          : `Lỗi mở webcam: ${e?.message || e}`
        onError(msg)
      }
    })()
    return () => { cancelled = true; stopStream() }
  }, [active, onError, stopStream])

  const capture = () => {
    const v = videoRef.current
    const c = canvasRef.current
    if (!v || !c || !ready) return
    c.width  = v.videoWidth  || 1280
    c.height = v.videoHeight || 720
    const ctx = c.getContext('2d')
    if (!ctx) return
    ctx.drawImage(v, 0, 0, c.width, c.height)
    c.toBlob(b => {
      if (b) onCapture(new File([b], `webcam_${Date.now()}.jpg`, { type: 'image/jpeg' }))
    }, 'image/jpeg', 0.92)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{
        position: 'relative', background: '#000', borderRadius: 'var(--r-md)',
        overflow: 'hidden', aspectRatio: '4 / 3',
      }}>
        <video ref={videoRef} autoPlay muted playsInline
               style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        {!ready && active && (
          <div style={{
            position: 'absolute', inset: 0, display: 'flex',
            alignItems: 'center', justifyContent: 'center',
            color: '#fff', background: 'rgba(0,0,0,0.5)', fontSize: 12,
          }}>
            <RefreshCw size={16} className="animate-spin" />&nbsp; Đang khởi động…
          </div>
        )}
        {!active && (
          <div style={{
            position: 'absolute', inset: 0, display: 'flex',
            alignItems: 'center', justifyContent: 'center',
            color: 'var(--text-muted)', flexDirection: 'column', gap: 4,
          }}>
            <Video size={28} />
            <span style={{ fontSize: 11 }}>Webcam tắt</span>
          </div>
        )}
      </div>
      <button className="btn btn--primary btn--sm" disabled={!ready} onClick={capture}>
        <CameraIcon size={14} /> Chụp
      </button>
      <canvas ref={canvasRef} style={{ display: 'none' }} />
    </div>
  )
}

// ── Face capture/upload section (dùng chung Modal create + edit faces) ───────

interface FaceCaptureProps {
  shots: File[]
  onChange: (files: File[]) => void
  onError: (msg: string) => void
}

function FaceCaptureSection({ shots, onChange, onError }: FaceCaptureProps) {
  const [webcamOn, setWebcamOn] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)

  const previews = shots.map(f => ({ file: f, url: URL.createObjectURL(f) }))
  useEffect(() => () => previews.forEach(p => URL.revokeObjectURL(p.url)), [shots])

  const onAddFiles = (files: FileList | File[] | null) => {
    if (!files) return
    const arr = Array.from(files).filter(f => f.type.startsWith('image/'))
    if (arr.length === 0) {
      onError('Chỉ chấp nhận file ảnh JPEG/PNG')
      return
    }
    onChange([...shots, ...arr].slice(0, 10))
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        ⓘ Khuyến nghị 3-5 ảnh ở các góc/ánh sáng khác nhau · ≤10 ảnh, JPEG/PNG ≤15MB
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        {/* Webcam */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
            <Video size={12} />
            <strong style={{ fontSize: 11 }}>Webcam</strong>
            <button
              type="button"
              className={`btn btn--sm ${webcamOn ? 'btn--danger' : 'btn--ghost'}`}
              style={{ marginLeft: 'auto' }}
              onClick={() => setWebcamOn(o => !o)}
            >
              {webcamOn ? <X size={11} /> : <Video size={11} />}
              &nbsp;{webcamOn ? 'Tắt' : 'Bật'}
            </button>
          </div>
          <WebcamCapture
            active={webcamOn}
            onCapture={f => onAddFiles([f])}
            onError={onError}
          />
        </div>

        {/* Upload */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
            <Upload size={12} />
            <strong style={{ fontSize: 11 }}>Upload file</strong>
          </div>
          <div
            onClick={() => fileInputRef.current?.click()}
            onDragOver={e => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={e => {
              e.preventDefault(); setDragOver(false)
              onAddFiles(e.dataTransfer.files)
            }}
            style={{
              border: `2px dashed ${dragOver ? 'var(--brand)' : 'var(--border)'}`,
              borderRadius: 'var(--r-md)',
              padding: 16,
              textAlign: 'center',
              cursor: 'pointer',
              background: dragOver ? 'var(--brand)10' : 'var(--bg-elevated)',
              aspectRatio: '4 / 3',
              display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center', gap: 6,
            }}
          >
            <Upload size={28} style={{ color: 'var(--text-muted)' }} />
            <div style={{ fontSize: 12 }}>Kéo thả ảnh hoặc <strong>click</strong></div>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png"
              multiple
              style={{ display: 'none' }}
              onChange={e => onAddFiles(e.target.files)}
            />
          </div>
        </div>
      </div>

      {/* Gallery */}
      {previews.length > 0 && (
        <div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
            Đã chọn: {previews.length}/10
          </div>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(72px, 1fr))', gap: 4,
          }}>
            {previews.map((p, i) => (
              <div key={p.url} style={{ position: 'relative', aspectRatio: '1 / 1' }}>
                <img src={p.url}
                     style={{ width: '100%', height: '100%', objectFit: 'cover',
                              borderRadius: 'var(--r-sm)', border: '1px solid var(--border)' }} />
                <button
                  type="button"
                  onClick={() => onChange(shots.filter((_, idx) => idx !== i))}
                  style={{
                    position: 'absolute', top: 1, right: 1, width: 18, height: 18,
                    background: 'rgba(0,0,0,0.7)', color: '#fff', border: 'none',
                    borderRadius: '50%', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}
                >
                  <Trash2 size={10} />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Create User Modal (2 tabs) ───────────────────────────────────────────────

interface CreateModalProps {
  onClose: () => void
  onCreated: () => void
}

function CreateUserModal({ onClose, onCreated }: CreateModalProps) {
  const [form, setForm] = useState({
    person_id: '', name: '', role: 'staff', blacklist_reason: '',
  })
  const [shots, setShots] = useState<File[]>([])
  const [busy, setBusy]   = useState(false)
  const [err, setErr]     = useState('')
  const [progress, setProgress] = useState('')

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.person_id || !form.name) {
      setErr('Person ID và Họ tên là bắt buộc')
      return
    }
    setBusy(true); setErr(''); setProgress('Đang tạo user…')
    try {
      const created = await usersApi.create(form)
      if (shots.length > 0) {
        setProgress(`Đang đăng ký ${shots.length} ảnh khuôn mặt…`)
        if (shots.length === 1) {
          await enrollApi.uploadFace(created.id, shots[0])
        } else {
          await enrollApi.uploadFaces(created.id, shots)
        }
      }
      onCreated()
    } catch (e: any) {
      setErr(e?.message || 'Lỗi tạo user')
    } finally {
      setBusy(false); setProgress('')
    }
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
      <div className="card" style={{ width: 920, maxWidth: '96vw', maxHeight: '95vh', overflow: 'auto' }}
           onClick={e => e.stopPropagation()}>
        <div className="card__title" style={{ justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Plus size={16} /> Thêm người dùng mới
          </div>
          <button className="btn btn--ghost btn--sm" onClick={onClose} disabled={busy}>
            <X size={14} />
          </button>
        </div>

        <form onSubmit={submit}>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(280px, 1fr) minmax(360px, 1.2fr)',
            gap: 18,
            alignItems: 'start',
          }}>
            {/* ── Cột trái: Thông tin ──────────────────────────────────── */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{
                fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
                color: 'var(--text-muted)', letterSpacing: 0.5,
                display: 'flex', alignItems: 'center', gap: 6,
              }}>
                <UserIcon size={12} /> Thông tin cá nhân
              </div>
              <div className="form-group">
                <label style={{ fontSize: 11, fontWeight: 600 }}>Person ID *</label>
                <input className="input" required value={form.person_id}
                       onChange={e => setForm(f => ({ ...f, person_id: e.target.value }))}
                       placeholder="EMP001" />
              </div>
              <div className="form-group">
                <label style={{ fontSize: 11, fontWeight: 600 }}>Họ và tên *</label>
                <input className="input" required value={form.name}
                       onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                       placeholder="Nguyễn Văn A" />
              </div>
              <div className="form-group">
                <label style={{ fontSize: 11, fontWeight: 600 }}>Vai trò</label>
                <select className="input" value={form.role}
                        onChange={e => setForm(f => ({ ...f, role: e.target.value }))}>
                  <option value="staff">Nhân viên</option>
                  <option value="admin">Admin</option>
                  <option value="guest">Khách</option>
                  <option value="blacklist">Blacklist</option>
                </select>
              </div>
              {form.role === 'blacklist' && (
                <div className="form-group">
                  <label style={{ fontSize: 11, fontWeight: 600 }}>Lý do blacklist *</label>
                  <input className="input" required value={form.blacklist_reason}
                         onChange={e => setForm(f => ({ ...f, blacklist_reason: e.target.value }))} />
                </div>
              )}
            </div>

            {/* ── Cột phải: Khuôn mặt ──────────────────────────────────── */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{
                fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
                color: 'var(--text-muted)', letterSpacing: 0.5,
                display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'space-between',
              }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <CameraIcon size={12} /> Khuôn mặt
                  <span style={{ color: 'var(--text-muted)', textTransform: 'none', fontWeight: 500 }}>
                    (tuỳ chọn — có thể bổ sung sau)
                  </span>
                </span>
                {shots.length > 0 && (
                  <span className="badge badge--brand" style={{ fontSize: 10 }}>
                    {shots.length} ảnh
                  </span>
                )}
              </div>
              <FaceCaptureSection
                shots={shots}
                onChange={setShots}
                onError={setErr}
              />
            </div>
          </div>

          {err && (
            <div style={{
              color: 'var(--danger)', fontSize: 12, padding: '8px 12px',
              background: 'var(--danger)15', borderRadius: 'var(--r-md)', marginTop: 12,
            }}>❌ {err}</div>
          )}

          <div style={{
            display: 'flex', gap: 8, marginTop: 16,
            justifyContent: 'flex-end', alignItems: 'center',
            paddingTop: 12, borderTop: '1px solid var(--border)',
          }}>
            {progress && <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{progress}</span>}
            <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>Huỷ</button>
            <button type="submit" className="btn btn--primary" disabled={busy}>
              {busy ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
              &nbsp;{busy ? 'Đang lưu…' : `Lưu${shots.length > 0 ? ` + đăng ký ${shots.length} ảnh` : ''}`}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Edit Faces Modal (cho user đã có) ───────────────────────────────────────

function EditFacesModal({ user, onClose, onSaved }: {
  user: User; onClose: () => void; onSaved: () => void
}) {
  const [shots, setShots] = useState<File[]>([])
  const [busy, setBusy]   = useState(false)
  const [err, setErr]     = useState('')

  const submit = async () => {
    if (shots.length === 0) { setErr('Chưa có ảnh nào'); return }
    setBusy(true); setErr('')
    try {
      if (shots.length === 1) {
        await enrollApi.uploadFace(user.id, shots[0])
      } else {
        await enrollApi.uploadFaces(user.id, shots)
      }
      onSaved()
    } catch (e: any) { setErr(e?.message || 'Lỗi đăng ký') }
    finally { setBusy(false) }
  }

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, padding: 16 }}
      onClick={onClose}
    >
      <div className="card" style={{ width: 640, maxWidth: '95vw', maxHeight: '95vh', overflow: 'auto' }}
           onClick={e => e.stopPropagation()}>
        <div className="card__title" style={{ justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <CameraIcon size={16} /> Cập nhật khuôn mặt — {user.name}
          </div>
          <button className="btn btn--ghost btn--sm" onClick={onClose}>
            <X size={14} />
          </button>
        </div>
        {user.has_embedding && (
          <div style={{
            padding: '8px 12px', background: 'var(--brand)10',
            borderRadius: 'var(--r-md)', marginBottom: 10, fontSize: 12,
          }}>
            ⓘ User đã có embedding — upload ảnh mới sẽ <strong>thay thế</strong>.
          </div>
        )}
        <FaceCaptureSection shots={shots} onChange={setShots} onError={setErr} />
        {err && (
          <div style={{
            color: 'var(--danger)', fontSize: 12, padding: '8px 12px',
            background: 'var(--danger)15', borderRadius: 'var(--r-md)', marginTop: 10,
          }}>❌ {err}</div>
        )}
        <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'flex-end' }}>
          <button className="btn btn--ghost" onClick={onClose} disabled={busy}>Huỷ</button>
          <button className="btn btn--primary" onClick={submit} disabled={busy || shots.length === 0}>
            {busy ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
            &nbsp;Đăng ký {shots.length} ảnh
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Face Search Modal ────────────────────────────────────────────────────────

export function FaceSearchModal({ onClose }: { onClose: () => void }) {
  const [shots, setShots] = useState<File[]>([])
  const [busy, setBusy]   = useState(false)
  const [err, setErr]     = useState('')
  const [results, setResults] = useState<FaceMatch[]>([])
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const [galleries, setGalleries] = useState<Record<string, StrangerImage[]>>({})
  const [galLoading, setGalLoading] = useState<string | null>(null)
  const [lightbox, setLightbox] = useState<string | null>(null)

  const search = async () => {
    if (shots.length === 0) return
    setBusy(true); setErr(''); setResults([]); setExpandedKey(null); setGalleries({})
    try {
      const data = await faceSearchApi.search(shots[0], { limit: 10, min_similarity: 0.4 })
      setResults(data)
      if (data.length === 0) setErr('Không tìm thấy match nào (similarity ≥ 0.4)')
    } catch (e: any) {
      setErr(e?.message || 'Lỗi tìm kiếm')
    } finally { setBusy(false) }
  }

  const toggleExpand = async (key: string, strangerId?: string) => {
    if (expandedKey === key) { setExpandedKey(null); return }
    setExpandedKey(key)
    if (strangerId && !galleries[strangerId]) {
      setGalLoading(strangerId)
      try {
        const imgs = await strangersApi.listImages(strangerId, 60)
        setGalleries(g => ({ ...g, [strangerId]: imgs }))
      } catch { /* ignore */ }
      finally { setGalLoading(null) }
    }
  }

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, padding: 16 }}
      onClick={onClose}
    >
      <div className="card" style={{ width: 760, maxWidth: '95vw', maxHeight: '95vh', overflow: 'auto' }}
           onClick={e => e.stopPropagation()}>
        <div className="card__title" style={{ justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Search size={16} /> Tìm danh tính bằng ảnh
          </div>
          <button className="btn btn--ghost btn--sm" onClick={onClose}>
            <X size={14} />
          </button>
        </div>

        <FaceCaptureSection
          shots={shots.slice(0, 1)}
          onChange={f => setShots(f.slice(0, 1))}
          onError={setErr}
        />

        <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'flex-end' }}>
          <button className="btn btn--ghost" onClick={onClose}>Đóng</button>
          <button className="btn btn--primary" onClick={search} disabled={busy || shots.length === 0}>
            {busy ? <RefreshCw size={14} className="animate-spin" /> : <Search size={14} />}
            &nbsp;Tìm kiếm
          </button>
        </div>

        {err && (
          <div style={{
            color: 'var(--danger)', fontSize: 12, padding: '8px 12px',
            background: 'var(--danger)15', borderRadius: 'var(--r-md)', marginTop: 10,
          }}>❌ {err}</div>
        )}

        {results.length > 0 && (() => {
          const userMatches     = results.filter(m => m.type === 'user')
          const strangerMatches = results.filter(m => m.type === 'stranger')

          const renderCard = (m: FaceMatch, i: number) => {
            const sevPct = Math.round(m.similarity * 100)
            const cls = m.similarity >= 0.6 ? 'success'
                      : m.similarity >= 0.45 ? 'medium' : 'low'
            const isUser = m.type === 'user'
            const key = `${m.type}-${i}`
            const expandable = !isUser && !!m.stranger_id
            const isExpanded = expandedKey === key
            const gallery = (m.stranger_id && galleries[m.stranger_id]) || []
            return (
              <div key={key} style={{
                background: 'var(--bg-elevated)',
                borderRadius: 'var(--r-md)',
                border: '1px solid var(--border)',
                borderLeft: `4px solid var(--sev-${cls})`,
                overflow: 'hidden',
              }}>
              <div
                onClick={() => expandable && toggleExpand(key, m.stranger_id)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 12,
                  padding: 10, cursor: expandable ? 'pointer' : 'default',
                }}
              >
                <div style={{
                  width: 64, height: 64, background: '#000',
                  borderRadius: 'var(--r-md)', overflow: 'hidden', flexShrink: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  {m.last_image
                    ? <img src={detectImageUrl(m.last_image) || ''}
                           style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                    : <UserIcon size={28} style={{ color: 'var(--text-muted)' }} />}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: 13, fontWeight: 700, marginBottom: 3,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {isUser
                      ? m.name
                      : <>Người lạ <code style={{
                          fontSize: 11, background: 'var(--bg)',
                          padding: '1px 6px', borderRadius: 4,
                          color: 'var(--text-muted)',
                        }}>{m.stranger_id?.slice(0, 12)}</code></>}
                  </div>
                  <div style={{
                    fontSize: 11, color: 'var(--text-muted)',
                    display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center',
                  }}>
                    {isUser ? (
                      <>
                        <span><strong>ID:</strong> {m.person_id}</span>
                        {m.role && (
                          <span className={`badge badge--${ROLE_BADGE[m.role] || 'medium'}`}
                                style={{ fontSize: 10 }}>
                            {m.role}
                          </span>
                        )}
                      </>
                    ) : (
                      <span>
                        <strong>Camera:</strong>{' '}
                        {(m.cameras && m.cameras.length > 0) ? m.cameras.join(', ') : '—'}
                      </span>
                    )}
                  </div>
                </div>
                <div style={{ textAlign: 'right', flexShrink: 0 }}>
                  <div style={{
                    fontSize: 18, fontWeight: 700,
                    color: `var(--sev-${cls})`, lineHeight: 1,
                  }}>
                    {sevPct}%
                  </div>
                  <div style={{ fontSize: 9, color: 'var(--text-muted)', marginTop: 2 }}>
                    độ khớp
                  </div>
                  {expandable && (
                    <div style={{ fontSize: 9, color: 'var(--brand)', marginTop: 4 }}>
                      {isExpanded ? '▲ Ẩn ảnh' : `▼ Xem ảnh`}
                    </div>
                  )}
                </div>
              </div>

              {/* Expanded gallery cho stranger */}
              {isExpanded && expandable && (
                <div style={{
                  padding: 10, borderTop: '1px solid var(--border)',
                  background: 'var(--bg)',
                }}>
                  {galLoading === m.stranger_id ? (
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      <RefreshCw size={11} className="animate-spin" /> Đang tải ảnh…
                    </div>
                  ) : gallery.length === 0 ? (
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Chưa có ảnh nào cho người lạ này.
                    </div>
                  ) : (
                    <>
                      <div style={{
                        fontSize: 10, color: 'var(--text-muted)',
                        marginBottom: 6, fontWeight: 600,
                      }}>
                        {gallery.length} ảnh đã ghi nhận — click để phóng to
                      </div>
                      <div style={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fill, minmax(90px, 1fr))',
                        gap: 6,
                      }}>
                        {gallery.map((img, k) => {
                          const url = detectImageUrl(img.image_path)
                          return (
                            <div
                              key={k}
                              onClick={e => { e.stopPropagation(); if (url) setLightbox(url) }}
                              style={{
                                background: '#000', borderRadius: 'var(--r-sm)',
                                overflow: 'hidden', aspectRatio: '1 / 1',
                                cursor: url ? 'zoom-in' : 'default',
                                position: 'relative',
                              }}
                              title={`${img.source_id || '—'} · ${new Date(img.created_at).toLocaleString('vi-VN')}`}
                            >
                              {url && <img src={url} alt=""
                                           style={{ width: '100%', height: '100%', objectFit: 'cover' }} />}
                              <div style={{
                                position: 'absolute', bottom: 0, left: 0, right: 0,
                                background: 'linear-gradient(to top, rgba(0,0,0,0.85), transparent)',
                                color: '#fff', fontSize: 9,
                                padding: '8px 3px 2px',
                              }}>
                                {img.source_id || '—'}
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </>
                  )}
                </div>
              )}
              </div>
            )
          }

          const sectionHeader = (icon: ReactNode, label: string, count: number, color: string) => (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              fontSize: 12, fontWeight: 700, marginBottom: 8, marginTop: 14,
              color, textTransform: 'uppercase', letterSpacing: 0.5,
            }}>
              {icon}
              <span>{label}</span>
              <span style={{
                fontSize: 10, background: `${color}20`, color,
                padding: '2px 8px', borderRadius: 10,
              }}>{count}</span>
            </div>
          )

          return (
            <div style={{ marginTop: 8 }}>
              <div style={{
                fontSize: 11, color: 'var(--text-muted)',
                padding: '8px 12px', background: 'var(--bg)',
                borderRadius: 'var(--r-md)', marginBottom: 4,
              }}>
                Tìm thấy <strong style={{ color: 'var(--text)' }}>{results.length}</strong> kết quả khớp:{' '}
                {userMatches.length} danh tính · {strangerMatches.length} người lạ
              </div>

              {userMatches.length > 0 && (
                <>
                  {sectionHeader(<CheckCircle size={13} />,
                    'Danh tính đã đăng ký', userMatches.length, 'var(--success)')}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {userMatches.map(renderCard)}
                  </div>
                </>
              )}

              {strangerMatches.length > 0 && (
                <>
                  {sectionHeader(<UserX size={13} />,
                    'Người lạ đã từng nhận diện', strangerMatches.length, 'var(--warning, #d4a017)')}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {strangerMatches.map(renderCard)}
                  </div>
                </>
              )}
            </div>
          )
        })()}
      </div>

      {/* Lightbox phóng to ảnh */}
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

// ── Main UsersPage ───────────────────────────────────────────────────────────

export default function UsersPage() {
  const [filter, setFilter] = useState<'all' | 'staff' | 'blacklist'>('all')
  const [showCreate, setShowCreate]   = useState(false)
  const [editFaceUser, setEditFaceUser] = useState<User | null>(null)
  const [showSearch, setShowSearch]   = useState(false)

  const { data: users = [], error, isLoading, mutate } = useSWR(
    ['/api/users', filter],
    () => usersApi.list(filter === 'all' ? {} : { role: filter }),
  )

  const getRoleIcon = (role: string) => {
    switch (role) {
      case 'staff': return <UserIcon size={12} className="inline mr-1" />
      case 'admin': return <Shield size={12} className="inline mr-1" />
      case 'blacklist': return <Ban size={12} className="inline mr-1" />
      default: return <UserIcon size={12} className="inline mr-1" />
    }
  }

  const handleDeactivate = async (id: number, name: string) => {
    if (!confirm(`Vô hiệu hoá user "${name}"?`)) return
    await usersApi.deactivate(id); mutate()
  }
  const handleBlacklist = async (u: User) => {
    const reason = prompt(`Lý do blacklist "${u.name}":`)
    if (!reason) return
    await usersApi.update(u.id, { role: 'blacklist', blacklist_reason: reason })
    mutate()
  }
  const handleRemoveEmbedding = async (u: User) => {
    if (!confirm(`Xoá embedding khuôn mặt của ${u.name}?`)) return
    await enrollApi.remove(u.id); mutate()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8, margin: 0 }}>
            <Users size={22} color="var(--brand)" /> Danh tính + Khuôn mặt
          </h2>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
            Tạo user và đăng ký khuôn mặt cùng lúc
          </p>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {(['all', 'staff', 'blacklist'] as const).map(f => (
            <button key={f}
                    className={`btn btn--sm ${filter === f ? 'btn--primary' : 'btn--ghost'}`}
                    onClick={() => setFilter(f)}>
              {f === 'all' ? 'Tất cả' : f === 'staff' ? 'Nhân viên' : 'Blacklist'}
            </button>
          ))}
          <button className="btn btn--primary" onClick={() => setShowCreate(true)}>
            <Plus size={14} /> Thêm user
          </button>
        </div>
      </div>

      {/* Banner tìm kiếm khuôn mặt — nổi bật để user dễ thấy */}
      <div
        onClick={() => setShowSearch(true)}
        style={{
          display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px',
          background: 'linear-gradient(135deg, var(--brand)18, var(--brand)08)',
          border: '1px solid var(--brand)40', borderRadius: 'var(--r-lg)',
          cursor: 'pointer', transition: 'all 0.15s',
        }}
        onMouseEnter={e => (e.currentTarget.style.background = 'var(--brand)20')}
        onMouseLeave={e => (e.currentTarget.style.background = 'linear-gradient(135deg, var(--brand)18, var(--brand)08)')}
      >
        <div style={{
          width: 44, height: 44, borderRadius: '50%',
          background: 'var(--brand)25', flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Search size={20} color="var(--brand)" />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: 'var(--brand)' }}>
            Tìm kiếm khuôn mặt bằng ảnh
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
            Upload hoặc chụp ảnh → hệ thống so khớp với toàn bộ danh tính + người lạ đã nhận diện
          </div>
        </div>
        <button className="btn btn--primary btn--sm" style={{ flexShrink: 0 }}>
          Mở tìm kiếm →
        </button>
      </div>

      {error && (
        <div style={{ color: 'var(--danger)', fontSize: 12, padding: '8px 12px',
                      background: 'var(--danger)15', borderRadius: 'var(--r-md)' }}>
          ❌ {error.message}
        </div>
      )}

      {/* Table */}
      <div className="card" style={{ padding: 0 }}>
        <div className="table-wrap">
          {isLoading ? (
            <div className="empty-state"><Clock size={32} className="empty-state__icon" /> Đang tải…</div>
          ) : users.length === 0 ? (
            <div className="empty-state"><UserX size={32} className="empty-state__icon" /> Chưa có user</div>
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
                        ? <span className="badge badge--success"><CheckCircle size={11} className="inline mr-1" /> Đã đăng ký</span>
                        : <span className="badge badge--medium"><AlertTriangle size={11} className="inline mr-1" /> Chưa đăng ký</span>}
                    </td>
                    <td className="text-muted text-sm">{new Date(u.created_at).toLocaleDateString('vi-VN')}</td>
                    <td>
                      <div style={{ display: 'flex', gap: 4 }}>
                        <button className="btn btn--sm btn--primary"
                                onClick={() => setEditFaceUser(u)}
                                title={u.has_embedding ? 'Cập nhật khuôn mặt' : 'Đăng ký khuôn mặt'}>
                          <CameraIcon size={13} />
                        </button>
                        {u.has_embedding && (
                          <button className="btn btn--sm btn--ghost"
                                  onClick={() => handleRemoveEmbedding(u)}
                                  title="Xoá embedding">
                            <UserX size={13} />
                          </button>
                        )}
                        {u.role !== 'blacklist' && (
                          <button className="btn btn--sm btn--danger"
                                  onClick={() => handleBlacklist(u)} title="Blacklist">
                            <Ban size={13} />
                          </button>
                        )}
                        <button className="btn btn--sm btn--ghost"
                                onClick={() => handleDeactivate(u.id, u.name)} title="Vô hiệu hoá">
                          <Trash2 size={13} />
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

      {/* Modals */}
      {showCreate && (
        <CreateUserModal
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); mutate() }}
        />
      )}
      {editFaceUser && (
        <EditFacesModal
          user={editFaceUser}
          onClose={() => setEditFaceUser(null)}
          onSaved={() => { setEditFaceUser(null); mutate() }}
        />
      )}
      {showSearch && (
        <FaceSearchModal onClose={() => setShowSearch(false)} />
      )}
    </div>
  )
}
