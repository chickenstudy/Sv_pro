import { useState, useEffect, useCallback, useRef } from 'react'
import {
  ClipboardList, ShieldCheck, UserRoundX, Car, Lock, Clock, VenetianMask,
  Link2, Zap, RefreshCw, X, Camera as CameraIcon, CalendarDays, Search,
  Download, ChevronLeft, ChevronRight, SlidersHorizontal, Eye, Gauge, Swords,
  AlertTriangle, CheckCircle, User,
} from 'lucide-react'
import { eventsApi, AccessEvent, EventDetail, detectImageUrl } from '../api'

// ── Constants ─────────────────────────────────────────────────────────────────
const PAGE_SIZE = 30

const EVENT_TYPE_LABELS: Record<string, string> = {
  blacklist_person:   'Blacklist người',
  blacklist_vehicle:  'Blacklist xe',
  zone_denied:        'Vi phạm zone',
  time_denied:        'Vi phạm giờ',
  spoof_detected:     'Giả mạo khuôn mặt',
  object_linked:      'Liên kết xe-người',
  camera_tamper:      'Tamper camera',
  fighting:           'Phát hiện đánh nhau',
  lpr_recognition:    'Đọc biển số',
  face_recognition:   'Nhận diện khuôn mặt',
  stranger_detected:  'Người lạ',
}
const SEV_COLOR: Record<string, string> = {
  CRITICAL: 'var(--sev-critical)', HIGH: 'var(--sev-high)',
  MEDIUM:   'var(--sev-medium)',   LOW:  'var(--sev-low)',
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtTs(ts: string) {
  return new Date(ts).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
function todayISO() { return new Date().toISOString().split('T')[0] }
function toStartOfDay(d: string) { return `${d}T00:00:00+07:00` }
function toEndOfDay(d: string)   { return `${d}T23:59:59+07:00` }

function getEventIcon(type: string, size = 14) {
  switch (type) {
    case 'blacklist_person':  return <UserRoundX size={size} />
    case 'blacklist_vehicle': return <Car size={size} />
    case 'zone_denied':       return <Lock size={size} />
    case 'time_denied':       return <Clock size={size} />
    case 'spoof_detected':    return <VenetianMask size={size} />
    case 'object_linked':     return <Link2 size={size} />
    case 'camera_tamper':     return <Eye size={size} />
    case 'fighting':          return <Swords size={size} />
    case 'lpr_recognition':   return <Car size={size} />
    case 'face_recognition':  return <User size={size} />
    case 'stranger_detected': return <UserRoundX size={size} />
    default:                  return <Zap size={size} />
  }
}

function exportCSV(events: AccessEvent[]) {
  const header = ['ID', 'Loại', 'Mức độ', 'Đối tượng', 'Loại đối tượng', 'Camera', 'Lý do', 'Thời gian', 'Alert']
  const rows = events.map(e => [
    e.id, e.event_type, e.severity, e.entity_id ?? '', e.entity_type ?? '',
    e.camera_id ?? '', (e.reason ?? '').replace(/,/g, ';'),
    new Date(e.event_timestamp).toLocaleString('vi-VN'), e.alert_sent ? 'Đã gửi' : 'Chưa',
  ])
  const csv = [header, ...rows].map(r => r.map(v => `"${v}"`).join(',')).join('\n')
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url
  a.download = `events_${new Date().toISOString().slice(0, 10)}.csv`
  a.click(); URL.revokeObjectURL(url)
}

// ── Confidence bar ────────────────────────────────────────────────────────────
function ConfBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(value * 100)
  const color = pct >= 80 ? 'var(--success)' : pct >= 55 ? 'var(--warning)' : 'var(--danger)'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ width: 90, fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
        <Gauge size={11} /> {label}
      </div>
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ flex: 1, height: 6, background: 'var(--border)', borderRadius: 3, overflow: 'hidden' }}>
          <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 3 }} />
        </div>
        <span style={{ fontSize: 12, fontFamily: 'JetBrains Mono, monospace', color, minWidth: 32, textAlign: 'right' }}>{pct}%</span>
      </div>
    </div>
  )
}

// ── Detail modal ──────────────────────────────────────────────────────────────
function EventDetailModal({ event, detail, loading, onClose }: {
  event: AccessEvent; detail: EventDetail | null; loading: boolean; onClose: () => void
}) {
  const isRecog = detail?.source === 'recognition_log'
  const imgUrl = detectImageUrl(detail?.image_path ?? event.image_path)

  useEffect(() => {
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', fn); return () => document.removeEventListener('keydown', fn)
  }, [onClose])

  return (
    <div onClick={e => { if (e.target === e.currentTarget) onClose() }}
      style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,.75)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
      <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-xl)', width: '100%', maxWidth: 680, maxHeight: '90vh', overflow: 'auto', boxShadow: '0 24px 64px rgba(0,0,0,.6)' }}>
        {/* Header */}
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, position: 'sticky', top: 0, background: 'var(--bg-elevated)', zIndex: 1 }}>
          <span style={{ color: 'var(--brand)' }}>{getEventIcon(event.event_type, 16)}</span>
          <span style={{ fontWeight: 700, fontSize: 13, flex: 1 }}>
            {EVENT_TYPE_LABELS[event.event_type] ?? event.event_type.replace(/_/g, ' ')}
          </span>
          <span style={{ fontSize: 10, fontWeight: 700, color: SEV_COLOR[event.severity], background: `${SEV_COLOR[event.severity]}22`, border: `1px solid ${SEV_COLOR[event.severity]}`, borderRadius: 4, padding: '2px 8px' }}>{event.severity}</span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: 4 }}>
            <X size={16} />
          </button>
        </div>

        {loading ? (
          <div style={{ padding: 48, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <RefreshCw size={24} color="var(--brand)" style={{ animation: 'spin 1s linear infinite' }} />
          </div>
        ) : (
          <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
            {/* Image */}
            {imgUrl && (
              <div>
                <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 6, fontWeight: 600, letterSpacing: .5 }}>ẢNH NHẬN DIỆN</div>
                <img src={imgUrl} alt="detection"
                  onError={e => { (e.target as HTMLImageElement).parentElement!.style.display = 'none' }}
                  style={{ width: '100%', maxHeight: 260, objectFit: 'contain', borderRadius: 'var(--r-md)', border: '1px solid var(--border)', background: 'var(--bg-surface)' }} />
              </div>
            )}

            {/* Common metadata */}
            <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', overflow: 'hidden' }}>
              {[
                { label: 'ID', value: event.id, mono: true },
                { label: 'Camera', value: event.camera_id ?? '---', icon: <CameraIcon size={11} /> },
                { label: 'Thời gian', value: fmtTs(event.event_timestamp), icon: <Clock size={11} /> },
                { label: 'Đối tượng', value: event.entity_id ?? '---' },
                { label: 'Loại', value: event.entity_type ?? '---' },
                { label: 'Lý do', value: event.reason ?? '---' },
              ].map((row, i, arr) => (
                <div key={row.label} style={{ display: 'flex', padding: '8px 14px', borderBottom: i < arr.length - 1 ? '1px solid var(--border)' : 'none', alignItems: 'center', gap: 6 }}>
                  <div style={{ width: 90, fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
                    {row.icon} {row.label}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-primary)', fontFamily: row.mono ? 'JetBrains Mono, monospace' : undefined, wordBreak: 'break-all' }}>{row.value}</div>
                </div>
              ))}
            </div>

            {/* Recognition log extras */}
            {isRecog && detail && (
              <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: .5, marginBottom: 2 }}>KẾT QUẢ AI</div>
                {(detail.fr_confidence ?? detail.match_score) != null && (
                  <ConfBar label="FR Score" value={(detail.fr_confidence ?? detail.match_score)!} />
                )}
                {detail.ocr_confidence != null && (
                  <ConfBar label="OCR" value={detail.ocr_confidence} />
                )}
                {detail.plate_number && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{ width: 90, fontSize: 11, color: 'var(--text-muted)' }}>Biển số</div>
                    <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 800, fontSize: 16, letterSpacing: 2 }}>{detail.plate_number}</span>
                    {detail.plate_category && <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>({detail.plate_category})</span>}
                  </div>
                )}
                {detail.person_name && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{ width: 90, fontSize: 11, color: 'var(--text-muted)' }}>Tên</div>
                    <span style={{ fontSize: 13, fontWeight: 600 }}>{detail.person_name}</span>
                    {detail.person_role && <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>({detail.person_role})</span>}
                  </div>
                )}
                {detail.is_stranger && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', background: 'var(--danger-glow)', border: '1px solid var(--danger)', borderRadius: 'var(--r-sm)', fontSize: 12, color: 'var(--danger)' }}>
                    <AlertTriangle size={13} /> Người lạ — chưa có trong cơ sở dữ liệu
                  </div>
                )}
                {detail.is_stranger === false && detail.person_name && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', background: 'var(--success-glow)', border: '1px solid var(--success)', borderRadius: 'var(--r-sm)', fontSize: 12, color: 'var(--success)' }}>
                    <CheckCircle size={13} /> Đã nhận diện
                  </div>
                )}
              </div>
            )}

            {/* Alert status */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
              {event.alert_sent
                ? <span style={{ display: 'flex', alignItems: 'center', gap: 5, color: 'var(--success)' }}><CheckCircle size={13} /> Đã gửi cảnh báo</span>
                : <span style={{ color: 'var(--text-muted)' }}>Chưa gửi cảnh báo</span>
              }
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Pagination ────────────────────────────────────────────────────────────────
function Pagination({ page, hasMore, onChange }: { page: number; hasMore: boolean; onChange: (p: number) => void }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '10px 0' }}>
      <button onClick={() => onChange(page - 1)} disabled={page === 0}
        style={{ ...pgBtn, opacity: page === 0 ? 0.4 : 1 }}>
        <ChevronLeft size={14} /> Trang trước
      </button>
      <span style={{ fontSize: 12, color: 'var(--text-muted)', padding: '0 8px' }}>Trang {page + 1}</span>
      <button onClick={() => onChange(page + 1)} disabled={!hasMore}
        style={{ ...pgBtn, opacity: !hasMore ? 0.4 : 1 }}>
        Trang sau <ChevronRight size={14} />
      </button>
    </div>
  )
}
const pgBtn: React.CSSProperties = {
  height: 30, padding: '0 12px', background: 'var(--bg-elevated)', border: '1px solid var(--border)',
  borderRadius: 'var(--r-sm)', color: 'var(--text-secondary)', fontSize: 12, cursor: 'pointer',
  display: 'flex', alignItems: 'center', gap: 4, transition: 'background var(--t-quick)',
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function EventsPage() {
  const today = todayISO()
  const [severity, setSeverity] = useState('')
  const [eventType, setEventType] = useState('')
  const [cameraId, setCameraId] = useState('')
  const [search, setSearch] = useState('')
  const [fromDate, setFromDate] = useState(today)
  const [toDate, setToDate] = useState(today)
  const [page, setPage] = useState(0)

  const [events, setEvents] = useState<AccessEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  const [selected, setSelected] = useState<AccessEvent | null>(null)
  const [detail, setDetail] = useState<EventDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  // Derived camera list from loaded events
  const camerasRef = useRef<Set<string>>(new Set())
  const [cameras, setCameras] = useState<string[]>([])

  const load = useCallback(async (silent = false) => {
    silent ? setRefreshing(true) : setLoading(true)
    try {
      const data = await eventsApi.list({
        severity:   severity   || undefined,
        event_type: eventType  || undefined,
        camera_id:  cameraId   || undefined,
        from: toStartOfDay(fromDate),
        to:   toEndOfDay(toDate),
        limit:  PAGE_SIZE,
        offset: page * PAGE_SIZE,
      })
      setEvents(data)
      // Reset camera set on non-silent load (filter changed) then re-accumulate
      if (!silent) camerasRef.current = new Set()
      data.forEach(e => { if (e.camera_id) camerasRef.current.add(e.camera_id) })
      setCameras(Array.from(camerasRef.current).sort())
    } catch (e) { console.error(e) }
    finally { setLoading(false); setRefreshing(false) }
  }, [severity, eventType, cameraId, fromDate, toDate, page])

  useEffect(() => { load() }, [load])

  // Reset page when filters change (except page itself)
  const resetPage = useCallback(() => setPage(0), [])

  // Load detail on row click
  useEffect(() => {
    if (!selected) { setDetail(null); return }
    setDetailLoading(true)
    eventsApi.getDetail(selected.id)
      .then(setDetail).catch(() => setDetail(null))
      .finally(() => setDetailLoading(false))
  }, [selected])

  // Client-side search filter (entity_id + reason)
  const filtered = search.trim()
    ? events.filter(e => {
        const q = search.toLowerCase().trim()
        return (e.entity_id ?? '').toLowerCase().includes(q) ||
               (e.reason ?? '').toLowerCase().includes(q) ||
               (e.camera_id ?? '').toLowerCase().includes(q)
      })
    : events

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <ClipboardList size={16} color="var(--brand)" />
        <span style={{ fontWeight: 700, fontSize: 14 }}>Lịch sử Cảnh báo</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4, padding: '2px 6px' }}>
          {filtered.length} bản ghi{filtered.length === PAGE_SIZE ? '+' : ''}
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
          <button onClick={() => exportCSV(filtered)}
            style={{ height: 30, padding: '0 10px', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', cursor: 'pointer', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 5, fontSize: 12 }}>
            <Download size={12} /> Xuất CSV
          </button>
          <button onClick={() => load(true)} disabled={refreshing}
            style={{ height: 30, width: 30, background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', cursor: 'pointer', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <RefreshCw size={12} style={{ animation: refreshing ? 'spin 1s linear infinite' : 'none' }} />
          </button>
        </div>
      </div>

      {/* Filters */}
      <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '10px 14px', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        {/* Date range */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <CalendarDays size={11} color="var(--text-muted)" />
          <input type="date" value={fromDate} max={toDate}
            onChange={e => { setFromDate(e.target.value); resetPage() }}
            style={dateInput} />
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>→</span>
          <input type="date" value={toDate} min={fromDate}
            onChange={e => { setToDate(e.target.value); resetPage() }}
            style={dateInput} />
        </div>
        {/* Severity */}
        <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
          <SlidersHorizontal size={11} style={{ position: 'absolute', left: 7, color: 'var(--text-muted)', pointerEvents: 'none' }} />
          <select value={severity} onChange={e => { setSeverity(e.target.value); resetPage() }} style={selInput}>
            <option value="">Mọi mức độ</option>
            <option value="CRITICAL">Critical</option>
            <option value="HIGH">High</option>
            <option value="MEDIUM">Medium</option>
            <option value="LOW">Low</option>
          </select>
        </div>
        {/* Event type */}
        <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
          <Zap size={11} style={{ position: 'absolute', left: 7, color: 'var(--text-muted)', pointerEvents: 'none' }} />
          <select value={eventType} onChange={e => { setEventType(e.target.value); resetPage() }} style={selInput}>
            <option value="">Mọi loại</option>
            <option value="blacklist_person">Blacklist người</option>
            <option value="blacklist_vehicle">Blacklist xe</option>
            <option value="zone_denied">Vi phạm zone</option>
            <option value="time_denied">Vi phạm giờ</option>
            <option value="spoof_detected">Giả mạo khuôn mặt</option>
            <option value="object_linked">Liên kết xe-người</option>
            <option value="camera_tamper">Tamper camera</option>
            <option value="fighting">Đánh nhau</option>
            <option value="lpr_recognition">Đọc biển số</option>
            <option value="face_recognition">Nhận diện khuôn mặt</option>
            <option value="stranger_detected">Người lạ</option>
          </select>
        </div>
        {/* Camera */}
        {cameras.length > 0 && (
          <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
            <CameraIcon size={11} style={{ position: 'absolute', left: 7, color: 'var(--text-muted)', pointerEvents: 'none' }} />
            <select value={cameraId} onChange={e => { setCameraId(e.target.value); resetPage() }} style={selInput}>
              <option value="">Tất cả camera</option>
              {cameras.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        )}
        {/* Search */}
        <div style={{ position: 'relative' }}>
          <Search size={11} style={{ position: 'absolute', left: 7, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)', pointerEvents: 'none' }} />
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Tìm đối tượng / lý do..."
            style={{ paddingLeft: 24, paddingRight: 8, height: 30, background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', color: 'var(--text-primary)', fontSize: 12, outline: 'none', width: 180 }} />
        </div>
      </div>

      {/* Table */}
      <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
            <RefreshCw size={24} style={{ animation: 'spin 1s linear infinite' }} />
            <span style={{ fontSize: 12 }}>Đang tải...</span>
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 48, textAlign: 'center', color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
            <ShieldCheck size={36} strokeWidth={1} />
            <span style={{ fontSize: 13 }}>Không có sự kiện nào trong khoảng thời gian này</span>
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-surface)' }}>
                {['Mức độ', 'Loại sự kiện', 'Đối tượng', 'Camera', 'Lý do', 'Thời gian', 'Alert'].map(h => (
                  <th key={h} style={{ padding: '9px 12px', textAlign: 'left', fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: .5, whiteSpace: 'nowrap' }}>
                    {h.toUpperCase()}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map(evt => (
                <tr key={evt.id} onClick={() => setSelected(evt)}
                  style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer', transition: 'background var(--t-quick)' }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-hover)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <td style={{ padding: '8px 12px' }}>
                    <span style={{ fontSize: 10, fontWeight: 700, color: SEV_COLOR[evt.severity], background: `${SEV_COLOR[evt.severity]}22`, border: `1px solid ${SEV_COLOR[evt.severity]}`, borderRadius: 4, padding: '2px 7px', letterSpacing: .5 }}>
                      {evt.severity}
                    </span>
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5, color: 'var(--text-secondary)', fontSize: 12 }}>
                      {getEventIcon(evt.event_type, 12)}
                      {EVENT_TYPE_LABELS[evt.event_type] ?? evt.event_type.replace(/_/g, ' ')}
                    </div>
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    <div style={{ fontWeight: 600, fontFamily: 'JetBrains Mono, monospace', fontSize: 12 }}>{evt.entity_id ?? '—'}</div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{evt.entity_type ?? ''}</div>
                  </td>
                  <td style={{ padding: '8px 12px', fontSize: 11, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <CameraIcon size={10} /> {evt.camera_id ?? '—'}
                  </td>
                  <td style={{ padding: '8px 12px', maxWidth: 180 }}>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{evt.reason ?? '—'}</div>
                  </td>
                  <td style={{ padding: '8px 12px', fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                    {fmtTs(evt.event_timestamp)}
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    {evt.alert_sent
                      ? <span style={{ fontSize: 10, color: 'var(--success)', background: 'var(--success-glow)', border: '1px solid var(--success)', borderRadius: 4, padding: '2px 7px' }}>Đã gửi</span>
                      : <span style={{ fontSize: 10, color: 'var(--text-muted)', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 4, padding: '2px 7px' }}>Chưa</span>
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {!loading && (
        <Pagination page={page} hasMore={events.length === PAGE_SIZE} onChange={p => { setPage(p); window.scrollTo({ top: 0 }) }} />
      )}

      {/* Detail modal */}
      {selected && (
        <EventDetailModal
          event={selected}
          detail={detail}
          loading={detailLoading}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}

const dateInput: React.CSSProperties = {
  height: 30, background: 'var(--bg-surface)', border: '1px solid var(--border)',
  borderRadius: 'var(--r-sm)', color: 'var(--text-primary)', fontSize: 12, padding: '0 8px', outline: 'none',
}
const selInput: React.CSSProperties = {
  paddingLeft: 24, paddingRight: 8, height: 30, background: 'var(--bg-surface)',
  border: '1px solid var(--border)', borderRadius: 'var(--r-sm)',
  color: 'var(--text-primary)', fontSize: 12, outline: 'none', cursor: 'pointer',
}
