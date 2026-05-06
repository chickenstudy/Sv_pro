import { useCallback, useEffect, useRef, useState } from 'react'
import useSWR from 'swr'
import {
  camerasApi, eventsApi, detectImageUrl, isDevMode,
  type AccessEvent, type EventDetail,
} from '../api'
import {
  Activity, AlertOctagon, AlertCircle, AlertTriangle, CheckCircle,
  Camera as CameraIcon, ShieldCheck, Clock, Video, UserX, User as UserIcon, Car,
  ScanFace, PersonStanding, UserCheck, AlertCircle as BlacklistIcon,
  X, Cpu, Target, Eye, ShieldAlert, Hash,
  Timer, Users, Zap, TriangleAlert,
} from 'lucide-react'
import { CameraGrid } from '../components/CameraStream'

// ── Helpers ───────────────────────────────────────────────────────────────────

const SEV_COLOR: Record<string, string> = {
  CRITICAL: 'critical', HIGH: 'high', MEDIUM: 'medium', LOW: 'low',
}
const SEV_ICON: Record<string, React.ElementType> = {
  CRITICAL: AlertOctagon, HIGH: AlertCircle, MEDIUM: AlertTriangle, LOW: CheckCircle,
}

function fmtTime(ts: string) {
  try { return new Date(ts).toLocaleTimeString('vi-VN', { hour12: false }) }
  catch { return ts }
}
function fmtDate(ts: string) {
  try { return new Date(ts).toLocaleDateString('vi-VN') }
  catch { return '' }
}

// ── Tab definitions ───────────────────────────────────────────────────────────

const FACE_TYPES     = new Set(['face_recognition', 'stranger_detected', 'blacklist_person'])
const LPR_TYPES      = new Set(['lpr_recognition', 'blacklist_vehicle'])
const BEHAVIOR_TYPES = new Set([
  'behavior_alert', 'loitering', 'crowd_detected', 'fight_detected', 'intrusion',
  'fighting', 'camera_tamper', 'falling', 'fallen', 'covered_person',
])

type TabId = 'face' | 'lpr' | 'behavior'
const TABS: { id: TabId; label: string; icon: React.ElementType }[] = [
  { id: 'face',     label: 'Khuôn mặt', icon: ScanFace },
  { id: 'lpr',      label: 'Biển số xe', icon: Car },
  { id: 'behavior', label: 'Hành vi',    icon: PersonStanding },
]

// ── Sub-filters per tab (loại nghiệp vụ) ───────────────────────────────────────
// Mỗi chip lọc thêm trong buffer của tab. id 'all' = không lọc.
type SubFilter = { id: string; label: string; predicate: (ev: AccessEvent) => boolean }

const FACE_FILTERS: SubFilter[] = [
  { id: 'all',       label: 'Tất cả',     predicate: () => true },
  { id: 'known',     label: 'Người quen', predicate: ev => ev.event_type === 'face_recognition' },
  { id: 'stranger',  label: 'Người lạ',   predicate: ev => ev.event_type === 'stranger_detected' },
  { id: 'blacklist', label: 'Blacklist',  predicate: ev => ev.event_type === 'blacklist_person' },
]

const LPR_FILTERS: SubFilter[] = [
  { id: 'all',           label: 'Tất cả',           predicate: () => true },
  { id: 'O_TO_DAN_SU',   label: 'Ô tô dân sự',      predicate: ev => ev.reason === 'O_TO_DAN_SU' },
  { id: 'XE_MAY_DAN_SU', label: 'Xe máy dân sự',    predicate: ev => ev.reason === 'XE_MAY_DAN_SU' },
  { id: 'BIEN_CA_NHAN',  label: 'Biển cá nhân',     predicate: ev => ev.reason === 'BIEN_CA_NHAN' },
  { id: 'unknown',       label: 'Không xác định',
    predicate: ev => ev.event_type === 'lpr_recognition' &&
      (ev.reason === 'KHONG_XAC_DINH' || ev.reason === 'UNKNOWN' || !ev.reason) },
  { id: 'blacklist',     label: 'Cảnh báo',         predicate: ev => ev.event_type === 'blacklist_vehicle' },
]

const BEHAVIOR_FILTERS: SubFilter[] = [
  { id: 'all',            label: 'Tất cả',     predicate: () => true },
  { id: 'falling',        label: 'Đang ngã',   predicate: ev => ev.event_type === 'falling' },
  { id: 'fallen',         label: 'Đã ngã',     predicate: ev => ev.event_type === 'fallen' },
  { id: 'fighting',       label: 'Đánh nhau',  predicate: ev => ev.event_type === 'fighting' || ev.event_type === 'fight_detected' },
  { id: 'covered_person', label: 'Che thân',   predicate: ev => ev.event_type === 'covered_person' },
  { id: 'camera_tamper',  label: 'Phá camera', predicate: ev => ev.event_type === 'camera_tamper' },
  { id: 'intrusion',      label: 'Xâm nhập',   predicate: ev => ev.event_type === 'intrusion' },
  { id: 'loitering',      label: 'Lảng vảng',  predicate: ev => ev.event_type === 'loitering' },
  { id: 'crowd',          label: 'Đám đông',   predicate: ev => ev.event_type === 'crowd_detected' },
]

const SUB_FILTERS: Record<TabId, SubFilter[]> = {
  face:     FACE_FILTERS,
  lpr:      LPR_FILTERS,
  behavior: BEHAVIOR_FILTERS,
}
// ── Event Detail Modal ────────────────────────────────────────────────────────

function ConfidenceBar({ value, label, color = 'var(--brand)' }: { value: number; label: string; color?: string }) {
  const pct = Math.round(Math.min(value, 1) * 100)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
        <span style={{ color: 'var(--text-muted)' }}>{label}</span>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, color }}>{pct}%</span>
      </div>
      <div style={{ height: 6, background: 'var(--bg)', borderRadius: 99, overflow: 'hidden' }}>
        <div style={{
          height: '100%', width: `${pct}%`,
          background: pct >= 80 ? 'var(--success)' : pct >= 50 ? 'var(--warning)' : 'var(--danger)',
          borderRadius: 99, transition: 'width 0.5s ease',
        }} />
      </div>
    </div>
  )
}

function Row({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, fontSize: 12, padding: '5px 0', borderBottom: '1px solid var(--border)' }}>
      <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>{label}</span>
      <span style={{ fontFamily: mono ? 'JetBrains Mono, monospace' : undefined, textAlign: 'right', wordBreak: 'break-word', overflowWrap: 'anywhere' }}>
        {value}
      </span>
    </div>
  )
}

function EventDetailModal({ eventId, onClose }: { eventId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<EventDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    eventsApi.getDetail(eventId)
      .then(d => { setDetail(d); setLoading(false) })
      .catch(e => { setError(e.message || 'Lỗi tải dữ liệu'); setLoading(false) })
  }, [eventId])

  // Close on Escape
  useEffect(() => {
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [onClose])

  // BE trả top-level (post migration events.py refactor) + fallback metadata_json
  // để backward compat với event_id cũ / recognition_logs pre-refactor.
  const imgUrl = detail?.image_path
    ? detectImageUrl(detail.image_path)
    : detail?.metadata?.image_path
      ? detectImageUrl(detail.metadata.image_path)
      : null
  const plateImgUrl = detail?.plate_image_path
    ? detectImageUrl(detail.plate_image_path)
    : detail?.metadata?.plate_image_path
      ? detectImageUrl(detail.metadata.plate_image_path)
      : null

  const isRecLog = detail?.source === 'recognition_log'
  // fr_confidence là canonical name, match_score giữ cho backward compat.
  const matchScore   = detail?.fr_confidence ?? detail?.match_score
  const ocrConf      = detail?.ocr_confidence
  const isStranger   = detail?.is_stranger
  const personName   = detail?.person_name   ?? detail?.metadata?.person_name
  const personRole   = detail?.person_role   ?? detail?.metadata?.person_role
  const SEV_CLS: Record<string, string> = { CRITICAL: 'critical', HIGH: 'high', MEDIUM: 'medium', LOW: 'low' }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.75)',
        backdropFilter: 'blur(4px)',
        zIndex: 1000,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: 24,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: '100%', maxWidth: 560,
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-bright)',
          borderRadius: 'var(--r-xl)',
          overflow: 'hidden',
          boxShadow: '0 24px 64px rgba(0,0,0,0.8)',
          animation: 'modalIn 0.15s ease',
          maxHeight: '90vh',
          display: 'flex', flexDirection: 'column',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '12px 16px', borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Cpu size={16} style={{ color: 'var(--brand)' }} />
            <span style={{ fontWeight: 700, fontSize: 14 }}>Chi tiết AI Detection</span>
            {detail?.source && (
              <span style={{ fontSize: 10, background: 'var(--bg-elevated)', color: 'var(--text-muted)', padding: '1px 6px', borderRadius: 4 }}>
                {isRecLog ? 'recognition_log' : 'access_event'}
              </span>
            )}
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: 4 }}>
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div style={{ overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
          {loading && (
            <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)' }}>
              <div className="spinner" style={{ margin: '0 auto 12px' }} />
              Đang tải dữ liệu...
            </div>
          )}
          {error && <div style={{ color: 'var(--danger)', textAlign: 'center', padding: 20 }}>⚠️ {error}</div>}

          {detail && (
            <>
              {/* Detection image — vehicle (lớn) + plate crop (nhỏ) cho LPR */}
              {(imgUrl || plateImgUrl) && (
                <div style={{ display: 'grid', gridTemplateColumns: plateImgUrl ? '1fr 120px' : '1fr', gap: 8 }}>
                  {imgUrl && (
                    <div style={{ borderRadius: 'var(--r-md)', overflow: 'hidden', background: '#000', maxHeight: 200 }}>
                      <img src={imgUrl} alt="detection" onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                        style={{ width: '100%', maxHeight: 200, objectFit: 'contain', display: 'block' }} />
                    </div>
                  )}
                  {plateImgUrl && (
                    <div style={{ borderRadius: 'var(--r-md)', overflow: 'hidden', background: '#000', border: '2px solid var(--brand)40', display: 'flex', alignItems: 'center', justifyContent: 'center', maxHeight: 200 }}>
                      <img src={plateImgUrl} alt="plate" onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                        style={{ width: '100%', maxHeight: 200, objectFit: 'contain', display: 'block' }} />
                    </div>
                  )}
                </div>
              )}

              {/* Confidence scores — phần quan trọng nhất */}
              {(matchScore != null || ocrConf != null) && (
                <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--r-md)', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                    <Target size={13} style={{ color: 'var(--brand)' }} />
                    <span style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.6px', color: 'var(--text-secondary)' }}>
                      Độ tin cậy AI
                    </span>
                  </div>
                  {matchScore != null && <ConfidenceBar value={matchScore} label="Face Match Score" />}
                  {ocrConf != null && <ConfidenceBar value={ocrConf} label="OCR Confidence (LPR)" />}
                </div>
              )}

              {/* Identity */}
              <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--r-md)', padding: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                  <Eye size={13} style={{ color: 'var(--brand)' }} />
                  <span style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.6px', color: 'var(--text-secondary)' }}>
                    Nhận diện
                  </span>
                </div>

                {isRecLog ? (
                  <>
                    <Row label="Trạng thái" value={
                      <span style={{ color: isStranger ? '#f97316' : 'var(--success)', fontWeight: 600 }}>
                        {isStranger ? '⚠ Người lạ' : '✓ Người quen'}
                      </span>
                    } />
                    {(personName || detail.person_id) && (
                      <Row label="Tên / ID" value={safeText(personName || detail.person_id) || '—'} />
                    )}
                    {personRole && <Row label="Vai trò" value={personRole} />}
                    {detail.label && <Row label="Label AI" value={detail.label} mono />}
                    {detail.plate_number && <Row label="Biển số" value={detail.plate_number} mono />}
                    {detail.plate_category && <Row label="Loại xe" value={detail.plate_category} />}
                  </>
                ) : (
                  <>
                    {detail.event_type && <Row label="Loại sự kiện" value={detail.event_type.replace(/_/g, ' ')} mono />}
                    {detail.entity_type && <Row label="Loại đối tượng" value={detail.entity_type} />}
                    {detail.entity_id && <Row label="ID đối tượng" value={detail.entity_id} mono />}
                    {detail.severity && (
                      <Row label="Mức độ" value={
                        <span className={`badge badge--${SEV_CLS[detail.severity] ?? 'low'}`}>{detail.severity}</span>
                      } />
                    )}
                    {detail.reason && <Row label="Lý do" value={detail.reason} />}
                    <Row label="Alert đã gửi" value={detail.alert_sent ? '✓ Đã gửi' : '✗ Chưa gửi'} />
                  </>
                )}
              </div>

              {/* Camera & time */}
              <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--r-md)', padding: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                  <CameraIcon size={13} style={{ color: 'var(--brand)' }} />
                  <span style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.6px', color: 'var(--text-secondary)' }}>
                    Camera & Thời gian
                  </span>
                </div>
                <Row label="Camera ID" value={detail.camera_id ?? '—'} mono />
                <Row label="Source ID" value={detail.source_id ?? '—'} mono />
                <Row label="Thời điểm" value={
                  new Date(detail.event_timestamp).toLocaleString('vi-VN', { hour12: false })
                } mono />
              </div>

              {/* Metadata JSON */}
              {detail.metadata && Object.keys(detail.metadata).length > 0 && (
                <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--r-md)', padding: 12 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                    <Hash size={13} style={{ color: 'var(--brand)' }} />
                    <span style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.6px', color: 'var(--text-secondary)' }}>
                      Metadata AI
                    </span>
                  </div>
                  {Object.entries(detail.metadata)
                    .filter(([k]) => k !== 'image_path')
                    .map(([k, v]) => (
                      <Row key={k} label={k} value={typeof v === 'object' ? JSON.stringify(v) : String(v)} mono />
                    ))
                  }
                </div>
              )}

              {/* Event ID */}
              <div style={{ textAlign: 'center', fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono, monospace' }}>
                ID: {detail.id}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Face Event Card ───────────────────────────────────────────────────────────
// Layout: [ảnh detect] | [badge tròn] | [hồ sơ]
// Người lạ  → ảnh chụp + silhouette "Chưa đăng ký"
// Người quen → ảnh chụp + avatar + tên/role
// Blacklist  → viền đỏ + badge cảnh báo

function FaceEventCard({ ev, isNew, onClick }: { ev: AccessEvent; isNew: boolean; onClick: () => void }) {
  const isStranger  = ev.event_type === 'stranger_detected'
  const isBlacklist = ev.event_type === 'blacklist_person'
  const isKnown     = ev.event_type === 'face_recognition'

  const imgUrl     = detectImageUrl(ev.image_path)
  const reasonStr  = typeof ev.reason === 'string' ? ev.reason : ''
  const personName = reasonStr && !['face', 'person', 'unknown'].includes(reasonStr.toLowerCase()) ? reasonStr : null
  const personId   = ev.entity_id ?? '—'

  const badgeColor = isBlacklist
    ? 'var(--sev-critical)'
    : isStranger
      ? '#f97316'
      : 'var(--brand)'

  const BadgeIcon = isBlacklist ? BlacklistIcon : isStranger ? UserX : UserCheck
  const badgeLabel = isBlacklist ? 'BLACKLIST' : isStranger ? 'Người lạ' : 'Người quen'

  const initial = ((personName?.[0] || personId?.[0] || '?')).toUpperCase()

  return (
    <div
      onClick={onClick}
      style={{
        borderRadius: 'var(--r-md)',
        border: `1px solid ${isBlacklist ? 'var(--sev-critical)50' : isStranger ? '#f9731630' : 'var(--border)'}`,
        background: 'var(--bg-elevated)',
        overflow: 'hidden',
        transition: 'box-shadow 0.15s, border-color 0.15s',
        boxShadow: isNew ? '0 0 0 2px var(--brand), 0 0 16px var(--brand)40' : 'none',
        cursor: 'pointer',
      }}
      onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--brand)80')}
      onMouseLeave={e => (e.currentTarget.style.borderColor = isBlacklist ? 'var(--sev-critical)50' : isStranger ? '#f9731630' : 'var(--border)')}
    >
      <div style={{ display: 'grid', gridTemplateColumns: '76px 1fr 76px' }}>

        {/* ── Left: ảnh detect từ camera ── */}
        <div style={{
          background: '#000',
          aspectRatio: '3/4',
          position: 'relative',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          overflow: 'hidden',
        }}>
          {imgUrl ? (
            <img
              src={imgUrl}
              alt="detection"
              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
          ) : (
            <UserX size={28} style={{ color: '#333' }} />
          )}
          {/* timestamp overlay */}
          <div style={{
            position: 'absolute', bottom: 0, left: 0, right: 0,
            background: 'linear-gradient(transparent, rgba(0,0,0,0.8))',
            padding: '8px 4px 3px',
            fontSize: 10, color: '#bbb', textAlign: 'center',
            fontFamily: 'JetBrains Mono, monospace',
          }}>
            {fmtTime(ev.event_timestamp)}
          </div>
        </div>

        {/* ── Center: badge + camera info ── */}
        <div style={{
          display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center',
          padding: '10px 6px', gap: 8,
        }}>
          {/* Badge tròn */}
          <div style={{
            width: 54, height: 54,
            borderRadius: '50%',
            border: `2px solid ${badgeColor}`,
            background: `${badgeColor}18`,
            display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center',
            boxShadow: `0 0 10px ${badgeColor}50`,
          }}>
            <BadgeIcon size={16} style={{ color: badgeColor }} />
            <span style={{ fontSize: 8, fontWeight: 700, color: badgeColor, lineHeight: 1.2, textAlign: 'center', marginTop: 2 }}>
              {badgeLabel.toUpperCase()}
            </span>
          </div>

          {/* Confidence badge (face match score) */}
          {ev.match_score != null && (() => {
            const pct = Math.round(ev.match_score * 100)
            const color = pct >= 80 ? 'var(--success)' : pct >= 55 ? 'var(--warning)' : 'var(--danger)'
            return (
              <div title={`Face match score: ${pct}%`} style={{
                display: 'inline-flex', alignItems: 'center', gap: 3,
                fontSize: 9, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700,
                color, background: `${color}18`, border: `1px solid ${color}40`,
                borderRadius: 4, padding: '1px 5px',
              }}>
                <Target size={8} /> {pct}%
              </div>
            )
          })()}

          {/* Camera + ngày */}
          <div style={{ textAlign: 'center' }}>
            <div style={{
              display: 'inline-flex', alignItems: 'center', gap: 3,
              fontSize: 10, color: 'var(--text-muted)',
              background: 'var(--bg)', borderRadius: 4, padding: '1px 5px',
            }}>
              <CameraIcon size={9} /> {ev.camera_id ?? '?'}
            </div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>
              {fmtDate(ev.event_timestamp)}
            </div>
          </div>
        </div>

        {/* ── Right: hồ sơ ── */}
        <div style={{
          background: '#0a0f1a',
          display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center',
          padding: '8px 4px', gap: 5,
          borderLeft: '1px solid var(--border)',
        }}>
          {isStranger || isBlacklist ? (
            // Người lạ / blacklist → silhouette
            <>
              <div style={{
                width: 40, height: 40, borderRadius: '50%',
                background: '#1a1a2e',
                border: `1px dashed ${isBlacklist ? 'var(--sev-critical)60' : '#333'}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <UserX size={20} style={{ color: isBlacklist ? 'var(--sev-critical)' : '#444' }} />
              </div>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', textAlign: 'center', lineHeight: 1.3 }}>
                Chưa<br />đăng ký
              </div>
              <div style={{
                fontSize: 9, fontFamily: 'JetBrains Mono, monospace',
                color: '#333', textAlign: 'center',
                overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 68,
              }}>
                {personId.slice(0, 10)}
              </div>
            </>
          ) : (
            // Người quen → avatar tên
            <>
              <div style={{
                width: 40, height: 40, borderRadius: '50%',
                background: `linear-gradient(135deg, var(--brand), var(--accent))`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 16, fontWeight: 700, color: '#fff',
                boxShadow: '0 0 8px var(--brand)40',
              }}>
                {initial}
              </div>
              <div style={{
                fontSize: 10, fontWeight: 600, color: 'var(--text-primary)',
                textAlign: 'center', lineHeight: 1.3,
                overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 68,
              }}>
                {personName ?? personId}
              </div>
              <div style={{
                fontSize: 9, background: 'var(--brand)20', color: 'var(--brand)',
                border: '1px solid var(--brand)30', borderRadius: 99,
                padding: '1px 5px',
              }}>
                Đã đăng ký
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── LPR Event Card ────────────────────────────────────────────────────────────
// Layout: [ảnh xe] | [biển số lớn + category + status + camera·time]

function LprEventCard({ ev, isNew, onClick }: { ev: AccessEvent; isNew: boolean; onClick: () => void }) {
  const isBlacklist  = ev.event_type === 'blacklist_vehicle'
  const plateNumber  = ev.entity_id ?? '?'
  const _AI_LABELS = new Set(['car', 'motorcycle', 'truck', 'bus', 'vehicle', 'plate'])
  const reasonStr = typeof ev.reason === 'string' ? ev.reason : ''
  const plateCategory = reasonStr && !_AI_LABELS.has(reasonStr.toLowerCase()) ? reasonStr : null
  const imgUrl       = detectImageUrl(ev.image_path)

  const borderColor = isBlacklist
    ? 'var(--sev-critical)60'
    : isNew ? 'var(--brand)' : 'var(--border)'

  return (
    <div
      onClick={onClick}
      style={{
        borderRadius: 'var(--r-md)',
        border: `1px solid ${borderColor}`,
        background: 'var(--bg-elevated)',
        overflow: 'hidden',
        cursor: 'pointer',
        transition: 'border-color 0.15s, box-shadow 0.6s',
        boxShadow: isNew ? '0 0 0 2px var(--brand), 0 0 14px var(--brand)40' : 'none',
      }}
      onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--brand)70')}
      onMouseLeave={e => (e.currentTarget.style.borderColor = borderColor)}
    >
      <div style={{ display: 'grid', gridTemplateColumns: '80px 1fr' }}>

        {/* Left: vehicle crop */}
        <div style={{
          background: '#000', aspectRatio: '4/3',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          overflow: 'hidden', position: 'relative', flexShrink: 0,
        }}>
          {imgUrl ? (
            <img
              src={imgUrl} alt="vehicle"
              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
          ) : (
            <Car size={28} style={{ color: '#2a3a50' }} />
          )}
          {isBlacklist && (
            <div style={{
              position: 'absolute', top: 4, left: 4,
              background: 'var(--sev-critical)', borderRadius: 3,
              padding: '1px 5px', fontSize: 9, fontWeight: 700, color: '#fff',
            }}>
              ⚠ BL
            </div>
          )}
        </div>

        {/* Right: plate info */}
        <div style={{
          padding: '8px 10px',
          display: 'flex', flexDirection: 'column', gap: 5,
          borderLeft: `2px solid ${isBlacklist ? 'var(--sev-critical)' : 'var(--accent)'}`,
        }}>
          {/* Biển số — hero element */}
          <div style={{
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 16, fontWeight: 800, letterSpacing: 2,
            background: isBlacklist ? 'var(--sev-critical)15' : 'var(--bg)',
            color: isBlacklist ? 'var(--sev-critical)' : 'var(--text-primary)',
            border: `1px solid ${isBlacklist ? 'var(--sev-critical)40' : 'var(--border-bright)'}`,
            borderRadius: 'var(--r-sm)',
            padding: '4px 8px',
            textAlign: 'center',
          }}>
            {plateNumber}
          </div>

          {/* Category + blacklist label + OCR confidence */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' }}>
            {plateCategory && (
              <span style={{
                fontSize: 10, color: 'var(--text-muted)',
                background: 'var(--bg)', border: '1px solid var(--border)',
                borderRadius: 4, padding: '1px 6px',
              }}>
                {plateCategory}
              </span>
            )}
            {ev.ocr_confidence != null && (() => {
              const pct = Math.round(ev.ocr_confidence * 100)
              const color = pct >= 80 ? 'var(--success)' : pct >= 55 ? 'var(--warning)' : 'var(--danger)'
              return (
                <span title={`OCR confidence: ${pct}%`} style={{
                  display: 'inline-flex', alignItems: 'center', gap: 3,
                  fontSize: 9, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700,
                  color, background: `${color}18`, border: `1px solid ${color}40`,
                  borderRadius: 4, padding: '1px 5px',
                }}>
                  <Target size={8} /> {pct}%
                </span>
              )
            })()}
            {isBlacklist && (
              <span className="badge badge--critical" style={{ fontSize: 9 }}>
                <BlacklistIcon size={9} /> BLACKLIST
              </span>
            )}
          </div>

          {/* Camera · time */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            fontSize: 10, color: 'var(--text-muted)', marginTop: 'auto',
          }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
              <CameraIcon size={9} /> {ev.camera_id ?? '?'}
            </span>
            <span style={{ marginLeft: 'auto', fontFamily: 'JetBrains Mono, monospace' }}>
              {fmtTime(ev.event_timestamp)}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Behavior Event Card ───────────────────────────────────────────────────────
// Layout: [header strip severity] / [ảnh | zone·reason·track·time]

const BEHAVIOR_META: Record<string, { label: string; icon: React.ElementType; color: string }> = {
  loitering:        { label: 'Lảng vảng bất thường',     icon: Timer,         color: 'var(--sev-medium)' },
  intrusion:        { label: 'Xâm nhập khu vực cấm',     icon: ShieldAlert,   color: 'var(--sev-critical)' },
  crowd_detected:   { label: 'Phát hiện đám đông',        icon: Users,         color: 'var(--warning)' },
  fight_detected:   { label: 'Phát hiện ẩu đả / bạo lực', icon: Zap,           color: 'var(--sev-critical)' },
  fighting:         { label: 'Phát hiện đánh nhau (AI)',  icon: Zap,           color: 'var(--sev-critical)' },
  camera_tamper:    { label: 'Camera bị che / giả mạo',   icon: Eye,           color: 'var(--sev-high)' },
  falling:          { label: 'Người đang ngã',            icon: TriangleAlert, color: 'var(--sev-high)' },
  fallen:           { label: 'Người đã ngã',              icon: AlertOctagon,  color: 'var(--sev-critical)' },
  covered_person:   { label: 'Người che mặt',             icon: UserX,         color: 'var(--sev-medium)' },
  behavior_alert:   { label: 'Cảnh báo hành vi',          icon: TriangleAlert, color: 'var(--sev-high)' },
}

/** Render text an toàn — không bao giờ "[object Object]". */
function safeText(v: any): string {
  if (v == null) return ''
  if (typeof v === 'string' || typeof v === 'number') return String(v)
  try { return JSON.stringify(v) } catch { return '' }
}

function BehaviorThumb({ url }: { url: string }) {
  const [failed, setFailed] = useState(false)
  if (failed) {
    return (
      <div style={{
        background: '#0a0f1a', aspectRatio: '1',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: 'var(--text-muted)', flexShrink: 0,
      }}>
        <CameraIcon size={20} strokeWidth={1.2} />
      </div>
    )
  }
  return (
    <div style={{
      background: '#000', aspectRatio: '1',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      overflow: 'hidden', flexShrink: 0,
    }}>
      <img
        src={url} alt="behavior"
        style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
        onError={() => setFailed(true)}
      />
    </div>
  )
}

function BehaviorEventCard({ ev, isNew, onClick }: { ev: AccessEvent; isNew: boolean; onClick: () => void }) {
  const meta     = BEHAVIOR_META[ev.event_type] ?? BEHAVIOR_META.behavior_alert
  const BehIcon  = meta.icon
  const sevCls   = SEV_COLOR[ev.severity] ?? 'medium'
  const SevIcon  = SEV_ICON[ev.severity] ?? AlertTriangle
  const imgUrl   = detectImageUrl(ev.image_path)

  return (
    <div
      onClick={onClick}
      style={{
        borderRadius: 'var(--r-md)',
        border: `1px solid ${isNew ? 'var(--brand)' : meta.color + '40'}`,
        background: 'var(--bg-elevated)',
        overflow: 'hidden',
        cursor: 'pointer',
        transition: 'border-color 0.15s, box-shadow 0.6s',
        boxShadow: isNew ? '0 0 0 2px var(--brand), 0 0 14px var(--brand)40' : 'none',
      }}
      onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--brand)70')}
      onMouseLeave={e => (e.currentTarget.style.borderColor = isNew ? 'var(--brand)' : meta.color + '40')}
    >
      {/* Header strip */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 7,
        padding: '6px 10px',
        background: `${meta.color}18`,
        borderBottom: `1px solid ${meta.color}30`,
      }}>
        <BehIcon size={13} style={{ color: meta.color, flexShrink: 0 }} />
        <span style={{ fontSize: 12, fontWeight: 700, color: meta.color, flex: 1 }}>
          {meta.label}
        </span>
        <span className={`badge badge--${sevCls}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 9 }}>
          <SevIcon size={9} /> {ev.severity}
        </span>
      </div>

      {/* Body */}
      <div style={{ display: 'grid', gridTemplateColumns: imgUrl ? '64px 1fr' : '1fr' }}>
        {imgUrl && (
          <BehaviorThumb url={imgUrl} />
        )}

        <div style={{ padding: '7px 10px', display: 'flex', flexDirection: 'column', gap: 4 }}>
          {/* Camera */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--text-muted)' }}>
            <CameraIcon size={10} />
            <span>{ev.camera_id ?? '?'}</span>
            <span style={{ marginLeft: 'auto', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
              {fmtTime(ev.event_timestamp)}
            </span>
          </div>

          {/* Reason / zone info */}
          {ev.reason && (
            <div style={{ fontSize: 11, color: 'var(--text-primary)', lineHeight: 1.4, wordBreak: 'break-word', overflowWrap: 'anywhere' }}>
              {safeText(ev.reason)}
            </div>
          )}

          {/* Track ID */}
          {ev.entity_id && (
            <div style={{ fontSize: 10, fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-muted)' }}>
              Track: {ev.entity_id}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Behavior ghost preview (empty state) ──────────────────────────────────────

function BehaviorGhostPreview() {
  const ghosts = [
    { type: 'intrusion',      sev: 'CRITICAL', cam: 'Camera-01', reason: 'Zone: Khu vực hạn chế — Tầng B1' },
    { type: 'loitering',      sev: 'MEDIUM',   cam: 'Camera-03', reason: 'Đối tượng lảng vảng > 5 phút' },
    { type: 'crowd_detected', sev: 'HIGH',      cam: 'Camera-02', reason: 'Phát hiện 8+ người trong vùng ROI' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {/* Thông báo */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '8px 12px',
        background: 'var(--bg-elevated)', borderRadius: 'var(--r-md)',
        border: '1px dashed var(--border)',
        fontSize: 11, color: 'var(--text-muted)',
      }}>
        <PersonStanding size={14} style={{ opacity: 0.5 }} />
        Module hành vi chưa kích hoạt — preview bên dưới
      </div>

      {/* Ghost cards */}
      {ghosts.map((g, i) => {
        const meta    = BEHAVIOR_META[g.type] ?? BEHAVIOR_META.behavior_alert
        const BehIcon = meta.icon
        const SevIcon = SEV_ICON[g.sev] ?? AlertTriangle
        const sevCls  = SEV_COLOR[g.sev] ?? 'medium'
        return (
          <div key={i} style={{ opacity: 0.25, filter: 'blur(0.6px)', pointerEvents: 'none' }}>
            <div style={{
              borderRadius: 'var(--r-md)',
              border: `1px solid ${meta.color}40`,
              background: 'var(--bg-elevated)', overflow: 'hidden',
            }}>
              <div style={{
                display: 'flex', alignItems: 'center', gap: 7,
                padding: '6px 10px',
                background: `${meta.color}18`,
                borderBottom: `1px solid ${meta.color}30`,
              }}>
                <BehIcon size={13} style={{ color: meta.color }} />
                <span style={{ fontSize: 12, fontWeight: 700, color: meta.color, flex: 1 }}>
                  {meta.label}
                </span>
                <span className={`badge badge--${sevCls}`} style={{ fontSize: 9, display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                  <SevIcon size={9} /> {g.sev}
                </span>
              </div>
              <div style={{ padding: '7px 10px', display: 'flex', flexDirection: 'column', gap: 3 }}>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', gap: 4 }}>
                  <CameraIcon size={10} /> {g.cam}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-primary)' }}>{g.reason}</div>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Generic Event Item (LPR, v.v.) ────────────────────────────────────────────

function EventFeedItem({ ev, onClick }: { ev: AccessEvent; onClick: () => void }) {
  const isPlate  = ev.entity_type === 'plate'
  const Icon     = isPlate ? Car : UserIcon
  const SevIcon  = SEV_ICON[ev.severity] ?? AlertTriangle
  const sevCls   = SEV_COLOR[ev.severity] ?? 'low'
  const imgUrl   = detectImageUrl(ev.image_path)

  return (
    <div
      onClick={onClick}
      style={{
        display: 'grid', gridTemplateColumns: '52px 1fr', gap: 8,
        padding: 8, borderRadius: 'var(--r-md)',
        background: 'var(--bg-elevated)',
        borderLeft: `3px solid var(--sev-${sevCls})`,
        cursor: 'pointer', transition: 'background 0.15s',
      }}
      onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-hover)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'var(--bg-elevated)')}
    >
      <div style={{
        width: 52, height: 52, borderRadius: 'var(--r-sm)',
        background: '#000', overflow: 'hidden', flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        {imgUrl
          ? <img src={imgUrl} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }}
              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
          : <Icon size={22} style={{ color: 'var(--text-muted)' }} />
        }
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' }}>
          <span className={`badge badge--${sevCls}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
            <SevIcon size={9} /> {ev.severity}
          </span>
          <span style={{ fontSize: 12, fontWeight: 600 }}>
            {ev.entity_id ?? '—'}
          </span>
          {ev.reason && <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>· {ev.reason}</span>}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', gap: 6 }}>
          <span><CameraIcon size={9} style={{ display: 'inline', verticalAlign: '-1px' }} /> {ev.camera_id ?? '?'}</span>
          <span style={{ marginLeft: 'auto', fontFamily: 'JetBrains Mono, monospace' }}>
            {fmtTime(ev.event_timestamp)}
            <span style={{ opacity: 0.6 }}> {fmtDate(ev.event_timestamp)}</span>
          </span>
        </div>
      </div>
    </div>
  )
}

// ── Realtime events hook (SSE, per-tab buffers) ───────────────────────────────
// Mỗi tab có buffer riêng + activatedAt epoch ms. Event đến chỉ đẩy vào tab có
// activatedAt > 0 và đúng category. Tab chưa active = không hứng. Khi user click
// tab → activate(tabId) đặt activatedAt = now và clear buffer cũ → "nhấn mới bắt
// đầu hứng".

const BUFFER_LIMIT = 30   // tối đa per-tab để không vỡ DOM

function categoryOf(ev: AccessEvent): TabId | null {
  if (FACE_TYPES.has(ev.event_type))     return 'face'
  if (LPR_TYPES.has(ev.event_type))      return 'lpr'
  if (BEHAVIOR_TYPES.has(ev.event_type)) return 'behavior'
  return null
}

type TabBuffers = Record<TabId, AccessEvent[]>
type TabActivations = Record<TabId, number>   // epoch ms; 0 = chưa active

function useRealtimeEvents() {
  const [buffers, setBuffers]         = useState<TabBuffers>({ face: [], lpr: [], behavior: [] })
  const [activations, setActivations] = useState<TabActivations>({ face: 0, lpr: 0, behavior: 0 })
  const [connected, setConnected]     = useState(false)
  const [loading, setLoading]         = useState(true)
  const [newIds, setNewIds]           = useState<Set<string>>(new Set())
  const [retryIn, setRetryIn]         = useState(0)

  // Ref để event handler luôn đọc activations mới nhất mà không cần resubscribe SSE
  const activationsRef = useRef(activations)
  useEffect(() => { activationsRef.current = activations }, [activations])

  const flashNew = useCallback((ids: string[]) => {
    if (!ids.length) return
    setNewIds(new Set(ids))
    const t = setTimeout(() => setNewIds(new Set()), 3000)
    return () => clearTimeout(t)
  }, [])

  const activate = useCallback((tab: TabId) => {
    if (activationsRef.current[tab] > 0) return   // đã active → giữ nguyên buffer
    setActivations(prev => prev[tab] > 0 ? prev : { ...prev, [tab]: Date.now() })
    setBuffers(prev => ({ ...prev, [tab]: [] }))
  }, [])

  // Đẩy event vào tab tương ứng nếu tab đó đã được activate
  const ingest = useCallback((ev: AccessEvent, isFlash: boolean) => {
    const cat = categoryOf(ev)
    if (!cat) return
    const activatedAt = activationsRef.current[cat]
    if (activatedAt === 0) return      // tab chưa active → drop
    setBuffers(prev => {
      const cur = prev[cat]
      if (cur.some(p => p.id === ev.id)) return prev
      return { ...prev, [cat]: [ev, ...cur].slice(0, BUFFER_LIMIT) }
    })
    if (isFlash) flashNew([ev.id])
  }, [flashNew])

  useEffect(() => {
    if (isDevMode()) { setLoading(false); setConnected(true); return }

    let es: EventSource
    let retryTimer: ReturnType<typeof setTimeout>
    let countdownTimer: ReturnType<typeof setInterval>

    const connect = () => {
      es = new EventSource(eventsApi.streamUrl())

      // Snapshot (lịch sử cũ): KHÔNG đổ vào tab nào — user nhấn mới bắt đầu hứng
      es.addEventListener('snapshot', () => {
        setLoading(false)
        setConnected(true)
        setRetryIn(0)
      })

      es.addEventListener('new_event', (e) => {
        const ev = JSON.parse(e.data) as AccessEvent
        ingest(ev, true)
      })

      es.onerror = () => {
        setConnected(false)
        es.close()
        let secs = 5
        setRetryIn(secs)
        countdownTimer = setInterval(() => {
          secs -= 1
          setRetryIn(secs)
          if (secs <= 0) clearInterval(countdownTimer)
        }, 1000)
        retryTimer = setTimeout(connect, 5000)
      }
    }

    connect()

    // Fallback polling 15s (bắt event không đi qua SSE). Chỉ lấy event mới hơn
    // activatedAt của tab tương ứng, không dump backlog.
    const pollTimer = setInterval(async () => {
      try {
        const fresh = await eventsApi.list({ limit: 20 })
        for (const ev of fresh) {
          const cat = categoryOf(ev)
          if (!cat) continue
          const activatedAt = activationsRef.current[cat]
          if (activatedAt === 0) continue
          const ts = ev.event_timestamp ? Date.parse(ev.event_timestamp) : Date.now()
          if (ts < activatedAt) continue
          ingest(ev, false)
        }
      } catch { /* ignore */ }
    }, 15_000)

    return () => {
      clearTimeout(retryTimer)
      clearInterval(countdownTimer)
      clearInterval(pollTimer)
      es?.close()
    }
  }, [ingest])

  return { buffers, activations, activate, connected, loading, newIds, retryIn }
}

// ── Dashboard Page ────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const [activeTab, setActiveTab]     = useState<TabId>('face')
  const [selectedId, setSelectedId]   = useState<string | null>(null)
  const [subFilters, setSubFilters]   = useState<Record<TabId, string>>({ face: 'all', lpr: 'all', behavior: 'all' })

  const { data: stats } = useSWR('/api/events/stats', eventsApi.stats, { refreshInterval: 15000 })
  const { data: cameras = [], isLoading: loadingCams } = useSWR('/api/cameras', camerasApi.list, { refreshInterval: 30000 })
  const { buffers, activations, activate, connected, loading: loadingEvents, newIds, retryIn } = useRealtimeEvents()

  // Default tab = face → activate ngay khi mount để user vừa mở dashboard có chỗ thấy event
  useEffect(() => { activate('face') }, [activate])

  const bySev               = stats?.by_severity ?? {}
  const tabBuffer           = buffers[activeTab]
  const tabFilters          = SUB_FILTERS[activeTab]
  const activeSubFilterId   = subFilters[activeTab]
  const activeSubFilter     = tabFilters.find(f => f.id === activeSubFilterId) ?? tabFilters[0]
  const visibleEvents       = activeSubFilter.id === 'all' ? tabBuffer : tabBuffer.filter(activeSubFilter.predicate)

  const handleTabClick = (tab: TabId) => {
    setActiveTab(tab)
    activate(tab)
  }
  const handleSubFilterClick = (id: string) => {
    setSubFilters(prev => ({ ...prev, [activeTab]: id }))
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {selectedId && <EventDetailModal eventId={selectedId} onClose={() => setSelectedId(null)} />}

      {/* ── Stat strip ── */}
      <div className="stats-grid">
        <div className="stat-card" style={{ '--card-accent': 'var(--brand)' } as any}>
          <Activity className="stat-card__icon" />
          <div className="stat-card__label">Tổng sự kiện hôm nay</div>
          <div className="stat-card__value">{stats?.total ?? '—'}</div>
        </div>
        {(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const).map(sev => {
          const Icon = SEV_ICON[sev]
          const cls  = SEV_COLOR[sev]
          return (
            <div key={sev} className="stat-card" style={{ '--card-accent': `var(--sev-${cls})` } as any}>
              <Icon className="stat-card__icon" />
              <div className="stat-card__label">{sev}</div>
              <div className="stat-card__value" style={{ color: `var(--sev-${cls})` }}>{bySev[sev] ?? 0}</div>
            </div>
          )
        })}
      </div>

      {/* ── SSE reconnect banner ── */}
      {!connected && !loadingEvents && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 14px', background: 'rgba(245,158,11,.1)', border: '1px solid var(--warning)', borderRadius: 'var(--r-md)', fontSize: 12, color: 'var(--warning)' }}>
          <AlertTriangle size={13} />
          <span>Mất kết nối realtime — đang kết nối lại{retryIn > 0 ? ` sau ${retryIn}s` : '...'}</span>
        </div>
      )}

      {/* ── 2-col: cameras + log ── (stack vertical khi viewport hẹp) */}
      <div className="dashboard-grid" style={{ display: 'grid', gap: 8, alignItems: 'stretch' }}>

        {/* LEFT — Live cameras */}
        <div className="card" style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <div className="card__title">
            <Video size={14} /> Camera trực tiếp
            <span className="text-muted" style={{ fontSize: 11, marginLeft: 'auto' }}>
              {cameras.filter(c => c.enabled).length}/{cameras.length} online
            </span>
          </div>
          {loadingCams
            ? <div className="empty-state"><Clock size={32} className="empty-state__icon" /> Đang tải...</div>
            : <CameraGrid cameras={cameras} />
          }
        </div>

        {/* RIGHT — Log AI Detect */}
        <div className="card" style={{ display: 'flex', flexDirection: 'column', minHeight: 560, minWidth: 0 }}>

          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Activity size={14} style={{ color: 'var(--text-secondary)' }} />
              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '.8px' }}>
                Log AI Detect
              </span>
              <span style={{
                width: 7, height: 7, borderRadius: '50%', display: 'inline-block',
                background: connected ? 'var(--success)' : 'var(--warning)',
                boxShadow: connected ? '0 0 6px var(--success)' : '0 0 6px var(--warning)',
                animation: connected ? 'pulse-green 2s infinite' : 'none',
              }} title={connected ? 'Realtime SSE' : 'Đang kết nối lại...'} />
            </div>
            <span style={{ fontSize: 10, color: connected ? 'var(--success)' : 'var(--warning)', fontWeight: 600 }}>
              {connected ? 'LIVE' : 'RECONNECTING...'}
            </span>
          </div>

          {/* Tab bar — counter chỉ đếm event đến SAU khi user click tab đó */}
          <div style={{ display: 'flex', gap: 2, background: 'var(--bg-elevated)', borderRadius: 'var(--r-md)', padding: 3, marginBottom: 8 }}>
            {TABS.map(tab => {
              const Icon       = tab.icon
              const isActive   = activeTab === tab.id
              const isReady    = activations[tab.id] > 0
              const count      = buffers[tab.id].length
              return (
                <button
                  key={tab.id}
                  onClick={() => handleTabClick(tab.id)}
                  title={isReady ? `${count} sự kiện kể từ khi bạn mở tab này` : 'Nhấn để bắt đầu nhận sự kiện'}
                  style={{
                    flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
                    padding: '5px 6px', borderRadius: 'var(--r-sm)', border: 'none', cursor: 'pointer',
                    fontSize: 11, fontWeight: 600, transition: 'all var(--t-quick)',
                    background: isActive ? 'var(--brand)' : 'transparent',
                    color: isActive ? '#fff' : 'var(--text-muted)',
                    minWidth: 0, overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis',
                  }}
                >
                  <Icon size={12} style={{ flexShrink: 0 }} />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{tab.label}</span>
                  <span style={{
                    fontSize: 10, borderRadius: 99, padding: '0 5px', minWidth: 18, textAlign: 'center',
                    background: isActive ? 'rgba(255,255,255,0.25)' : 'var(--bg-hover)',
                    color: isActive ? '#fff' : 'var(--text-muted)',
                    flexShrink: 0,
                  }}>
                    {isReady ? count : '—'}
                  </span>
                </button>
              )
            })}
          </div>

          {/* Sub-filter chips (loại nghiệp vụ trong tab hiện tại) */}
          {activations[activeTab] > 0 && (
            <div style={{
              display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 8,
              paddingBottom: 6, borderBottom: '1px solid var(--border)',
            }}>
              {tabFilters.map(f => {
                const isSel = f.id === activeSubFilter.id
                const cnt = f.id === 'all' ? tabBuffer.length : tabBuffer.filter(f.predicate).length
                return (
                  <button
                    key={f.id}
                    onClick={() => handleSubFilterClick(f.id)}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 4,
                      padding: '3px 8px', fontSize: 10, fontWeight: 600,
                      borderRadius: 99, cursor: 'pointer',
                      border: `1px solid ${isSel ? 'var(--brand)' : 'var(--border)'}`,
                      background: isSel ? 'var(--brand)' : 'transparent',
                      color: isSel ? '#fff' : 'var(--text-muted)',
                      transition: 'all var(--t-quick)',
                      whiteSpace: 'nowrap',
                    }}
                    title={`${cnt} sự kiện`}
                  >
                    {f.label}
                    {cnt > 0 && (
                      <span style={{
                        fontSize: 9, fontFamily: 'JetBrains Mono, monospace',
                        background: isSel ? 'rgba(255,255,255,0.25)' : 'var(--bg-hover)',
                        color: isSel ? '#fff' : 'var(--text-muted)',
                        borderRadius: 99, padding: '0 5px', minWidth: 14, textAlign: 'center',
                      }}>
                        {cnt}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          )}

          {/* Feed */}
          <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6, paddingRight: 8, maxHeight: 'calc(100vh - 260px)' }}>
            {/* Empty states */}
            {activeTab === 'behavior' && visibleEvents.length === 0 ? (
              <BehaviorGhostPreview />
            ) : activeTab === 'lpr' && visibleEvents.length === 0 ? (
              <div className="empty-state" style={{ padding: 32 }}>
                <Car size={32} className="empty-state__icon" style={{ opacity: 0.3 }} />
                <span style={{ fontSize: 12 }}>Chưa có nhận diện biển số</span>
              </div>
            ) : activeTab === 'face' && visibleEvents.length === 0 ? (
              <div className="empty-state" style={{ padding: 32 }}>
                <ShieldCheck size={32} className="empty-state__icon" style={{ color: 'var(--success)', opacity: 0.5 }} />
                <span style={{ fontSize: 12 }}>
                  {loadingEvents ? 'Đang chờ kết nối...' : 'Chưa có nhận diện khuôn mặt'}
                </span>
              </div>
            ) : visibleEvents.length === 0 ? (
              <div className="empty-state" style={{ padding: 32 }}>
                <ShieldCheck size={32} className="empty-state__icon" style={{ color: 'var(--success)' }} />
                Chưa có detection nào
              </div>

            /* Event cards — mỗi tab dùng component riêng */
            ) : activeTab === 'face' ? (
              visibleEvents.map(ev => (
                <FaceEventCard key={ev.id} ev={ev} isNew={newIds.has(ev.id)} onClick={() => setSelectedId(ev.id)} />
              ))
            ) : activeTab === 'lpr' ? (
              visibleEvents.map(ev => (
                <LprEventCard key={ev.id} ev={ev} isNew={newIds.has(ev.id)} onClick={() => setSelectedId(ev.id)} />
              ))
            ) : (
              visibleEvents.map(ev => (
                <BehaviorEventCard key={ev.id} ev={ev} isNew={newIds.has(ev.id)} onClick={() => setSelectedId(ev.id)} />
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
