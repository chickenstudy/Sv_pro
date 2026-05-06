import { useState, useEffect, useCallback } from 'react'
import {
  Activity, Camera as CameraIcon, Clock, AlertTriangle, Users,
  SlidersHorizontal, RefreshCw, MapPin, Timer, Zap, Eye,
  ShieldAlert, PersonStanding, X, ChevronLeft, ChevronRight,
  EyeOff, UserX, TriangleAlert,
} from 'lucide-react'
import { eventsApi, AccessEvent, EventDetail, detectImageUrl, isDevMode } from '../api'

const PAGE_SIZE = 12

function Pagination({ page, total, pageSize, onChange }: {
  page: number; total: number; pageSize: number; onChange: (p: number) => void
}) {
  const totalPages = Math.ceil(total / pageSize)
  if (totalPages <= 1) return null
  const pages: (number | '…')[] = []
  for (let i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || (i >= page - 1 && i <= page + 1)) pages.push(i)
    else if (pages[pages.length - 1] !== '…') pages.push('…')
  }
  const btn: React.CSSProperties = {
    minWidth: 28, height: 28, padding: '0 6px',
    background: 'var(--bg-elevated)', border: '1px solid var(--border)',
    borderRadius: 'var(--r-sm)', color: 'var(--text-secondary)',
    fontSize: 12, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
  }
  const active: React.CSSProperties = { background: 'var(--brand)', borderColor: 'var(--brand)', color: '#fff', fontWeight: 700 }
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4, padding: '14px 0' }}>
      <button onClick={() => onChange(page - 1)} disabled={page <= 1} style={{ ...btn, opacity: page <= 1 ? 0.4 : 1 }}>
        <ChevronLeft size={14} />
      </button>
      {pages.map((p, i) =>
        p === '…'
          ? <span key={`e${i}`} style={{ padding: '0 4px', color: 'var(--text-muted)', fontSize: 12 }}>…</span>
          : <button key={p} onClick={() => onChange(p as number)} style={{ ...btn, ...(p === page ? active : {}) }}>{p}</button>
      )}
      <button onClick={() => onChange(page + 1)} disabled={page >= totalPages} style={{ ...btn, opacity: page >= totalPages ? 0.4 : 1 }}>
        <ChevronRight size={14} />
      </button>
      <span style={{ marginLeft: 8, fontSize: 11, color: 'var(--text-muted)' }}>
        {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} / {total}
      </span>
    </div>
  )
}

// ── Behavior event types & metadata ──────────────────────────────────────────
//
// CANONICAL event_type strings phải KHỚP CHÍNH XÁC với AI core emit:
//   src/business/blacklist_engine.py::_process_behavior_alerts()
//   src/analytics/behavior_pyfunc.py → frame tag "behavior_alerts"
//
// 5 loại IMPLEMENTED (AI core phát):
//   fighting         — R3D-18 model phát hiện ẩu đả (src/analytics/behavior_engine.py)
//   camera_tamper    — ResNet phát hiện camera bị che
//   covered_person   — Custom YOLOv8 phát hiện người trùm kín mặt
//   fallen           — YOLOv8 fall_detector class "fallen" (ngã xuống)
//   falling          — YOLOv8 fall_detector class "falling" (đang ngã)
//
// 3 loại PLACEHOLDER (hiển thị trong UI nhưng AI chưa implement — coming soon):
//   loitering, zone_intrusion, crowd_gathering
// Các loại này KHÔNG được fetch qua API (tránh spam query trả rỗng).

const BEHAVIOR_TYPES = [
  'fighting', 'camera_tamper', 'covered_person', 'fallen', 'falling',
  'loitering', 'zone_intrusion', 'crowd_gathering',
] as const
type BehaviorType = typeof BEHAVIOR_TYPES[number]

// Chỉ type nào có trong mảng này mới thực sự query BE. 3 placeholder loại bỏ.
const IMPLEMENTED_BEHAVIOR_TYPES: readonly BehaviorType[] = [
  'fighting', 'camera_tamper', 'covered_person', 'fallen', 'falling',
]

const BEHAVIOR_META: Record<BehaviorType, {
  label: string; color: string; icon: React.ReactNode; desc: string; implemented: boolean
}> = {
  fighting: {
    label: 'Ẩu đả',
    color: 'var(--sev-critical)',
    icon: <Zap size={14} />,
    desc: 'AI phát hiện hành vi bạo lực giữa người với người',
    implemented: true,
  },
  camera_tamper: {
    label: 'Camera bị che',
    color: 'var(--danger)',
    icon: <EyeOff size={14} />,
    desc: 'Camera bị che kín ống kính / tampering',
    implemented: true,
  },
  covered_person: {
    label: 'Người che mặt',
    color: 'var(--danger)',
    icon: <UserX size={14} />,
    desc: 'Phát hiện người che/trùm kín mặt (mũ, khẩu trang đậy kín)',
    implemented: true,
  },
  fallen: {
    label: 'Té ngã',
    color: 'var(--sev-critical)',
    icon: <TriangleAlert size={14} />,
    desc: 'Phát hiện người đã ngã xuống đất',
    implemented: true,
  },
  falling: {
    label: 'Đang ngã',
    color: 'var(--warning)',
    icon: <PersonStanding size={14} />,
    desc: 'Phát hiện khoảnh khắc người đang ngã',
    implemented: true,
  },
  // ── Placeholder: AI core chưa implement — UI chỉ hiển thị, không query BE ──
  loitering: {
    label: 'Lảng vảng',
    color: 'var(--text-muted)',
    icon: <Timer size={14} />,
    desc: 'Người đứng quá lâu trong khu vực (coming soon)',
    implemented: false,
  },
  zone_intrusion: {
    label: 'Xâm nhập vùng cấm',
    color: 'var(--text-muted)',
    icon: <ShieldAlert size={14} />,
    desc: 'Xâm nhập khu vực cấm (coming soon)',
    implemented: false,
  },
  crowd_gathering: {
    label: 'Tụ tập đông người',
    color: 'var(--text-muted)',
    icon: <Users size={14} />,
    desc: 'Số lượng người vượt ngưỡng (coming soon)',
    implemented: false,
  },
}

function getBehaviorMeta(eventType: string) {
  return BEHAVIOR_META[eventType as BehaviorType] ?? {
    label: eventType,
    color: 'var(--text-muted)',
    icon: <Activity size={14} />,
    desc: '',
    implemented: false,
  }
}

// ── Mock data ────────────────────────────────────────────────────────────────

const _TS = (h: number, m: number, s: number) => {
  const d = new Date('2026-04-23T00:00:00+07:00')
  d.setHours(h, m, s)
  return d.toISOString()
}

// Mock data — canonical event_types khớp AI core (xem BEHAVIOR_META comment ở trên).
const MOCK_BEHAVIORS: AccessEvent[] = [
  { id: '1', event_type: 'fighting',         entity_type: 'behavior', entity_id: 'cam-01',    severity: 'HIGH',     camera_id: 'cam-01', source_id: 'cam-01', reason: 'Phát hiện đánh nhau (confidence: 78%)',      event_timestamp: _TS(14,10,0),  alert_sent: true,  image_path: null },
  { id: '2', event_type: 'camera_tamper',    entity_type: 'behavior', entity_id: 'cam-02',    severity: 'HIGH',     camera_id: 'cam-02', source_id: 'cam-02', reason: 'Camera bị che/tamper (confidence: 92%)',     event_timestamp: _TS(13,52,30), alert_sent: true,  image_path: null },
  { id: '3', event_type: 'covered_person',   entity_type: 'behavior', entity_id: 'cam-03',    severity: 'HIGH',     camera_id: 'cam-03', source_id: 'cam-03', reason: 'Phát hiện covered_person (confidence: 71%)', event_timestamp: _TS(13,40,15), alert_sent: false, image_path: null },
  { id: '4', event_type: 'fallen',           entity_type: 'behavior', entity_id: 'cam-01',    severity: 'HIGH',     camera_id: 'cam-01', source_id: 'cam-01', reason: 'Phát hiện fallen (confidence: 85%)',         event_timestamp: _TS(13,25,5),  alert_sent: true,  image_path: null },
  { id: '5', event_type: 'falling',          entity_type: 'behavior', entity_id: 'cam-04',    severity: 'MEDIUM',   camera_id: 'cam-04', source_id: 'cam-04', reason: 'Phát hiện falling (confidence: 67%)',        event_timestamp: _TS(12,58,44), alert_sent: false, image_path: null },
  { id: '6', event_type: 'fighting',         entity_type: 'behavior', entity_id: 'cam-02',    severity: 'HIGH',     camera_id: 'cam-02', source_id: 'cam-02', reason: 'Phát hiện đánh nhau (confidence: 82%)',      event_timestamp: _TS(12,42,20), alert_sent: true,  image_path: null },
  { id: '7', event_type: 'fallen',           entity_type: 'behavior', entity_id: 'cam-03',    severity: 'HIGH',     camera_id: 'cam-03', source_id: 'cam-03', reason: 'Phát hiện fallen (confidence: 90%)',         event_timestamp: _TS(12,30,0),  alert_sent: true,  image_path: null },
  { id: '8', event_type: 'covered_person',   entity_type: 'behavior', entity_id: 'cam-04',    severity: 'HIGH',     camera_id: 'cam-04', source_id: 'cam-04', reason: 'Phát hiện covered_person (confidence: 65%)', event_timestamp: _TS(12,15,33), alert_sent: false, image_path: null },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTime(ts: string) {
  return new Date(ts).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
function fmtFull(ts: string) {
  return new Date(ts).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

const SEV_COLOR: Record<string, string> = {
  CRITICAL: 'var(--sev-critical)',
  HIGH:     'var(--sev-high)',
  MEDIUM:   'var(--sev-medium)',
  LOW:      'var(--sev-low)',
}

function PlaceholderImage({ label, icon, aspectRatio = '16/9' }: { label?: string; icon?: React.ReactNode; aspectRatio?: string }) {
  return (
    <div style={{
      aspectRatio,
      background: 'var(--bg-surface)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--r-md)',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      gap: 6, color: 'var(--text-muted)',
    }}>
      {icon ?? <Eye size={24} strokeWidth={1} />}
      {label && <span style={{ fontSize: 10 }}>{label}</span>}
    </div>
  )
}

// ── Behavior card ─────────────────────────────────────────────────────────────

function BehaviorCard({ event, onClick }: { event: AccessEvent; onClick: () => void }) {
  const meta = getBehaviorMeta(event.event_type)
  const imgUrl = detectImageUrl(event.image_path)
  const isCritical = event.severity === 'CRITICAL'

  return (
    <div
      onClick={onClick}
      style={{
        background: 'var(--bg-elevated)',
        border: `1px solid ${isCritical ? 'var(--sev-critical)' : 'var(--border)'}`,
        borderRadius: 'var(--r-lg)',
        overflow: 'hidden',
        cursor: 'pointer',
        transition: 'transform var(--t-quick), box-shadow var(--t-quick)',
        boxShadow: isCritical ? `0 0 12px ${SEV_COLOR.CRITICAL}20` : 'var(--shadow-card)',
      }}
      onMouseEnter={e => {
        const el = e.currentTarget as HTMLDivElement
        el.style.transform = 'translateY(-2px)'
        el.style.boxShadow = `0 8px 24px rgba(0,0,0,.4)`
      }}
      onMouseLeave={e => {
        const el = e.currentTarget as HTMLDivElement
        el.style.transform = 'translateY(0)'
        el.style.boxShadow = isCritical ? `0 0 12px ${SEV_COLOR.CRITICAL}20` : 'var(--shadow-card)'
      }}
    >
      {/* Colored header strip */}
      <div style={{
        padding: '8px 12px',
        background: `${meta.color}18`,
        borderBottom: `1px solid ${meta.color}30`,
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <span style={{ color: meta.color, display: 'flex', alignItems: 'center' }}>{meta.icon}</span>
        <span style={{ fontSize: 12, fontWeight: 700, color: meta.color, flex: 1 }}>{meta.label}</span>
        <span style={{
          fontSize: 9, fontWeight: 800,
          color: SEV_COLOR[event.severity],
          background: `${SEV_COLOR[event.severity]}22`,
          border: `1px solid ${SEV_COLOR[event.severity]}`,
          borderRadius: 3, padding: '2px 5px', letterSpacing: .5,
        }}>{event.severity}</span>
        {event.alert_sent && (
          <span style={{
            fontSize: 9, fontWeight: 700,
            color: 'var(--warning)',
            background: 'var(--warning-glow)',
            border: '1px solid var(--warning)',
            borderRadius: 3, padding: '2px 5px', letterSpacing: .5,
          }}>ALERT</span>
        )}
      </div>

      {/* Image area */}
      <div style={{ padding: '10px 12px 0' }}>
        {imgUrl
          ? <img src={imgUrl} alt="behavior" onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
              style={{ width: '100%', aspectRatio: '16/9', objectFit: 'cover', borderRadius: 'var(--r-sm)', display: 'block' }} />
          : <PlaceholderImage label="Chưa có ảnh" aspectRatio="16/9" icon={<Eye size={20} strokeWidth={1} />} />
        }
      </div>

      {/* Info rows */}
      <div style={{ padding: '10px 12px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
        {event.reason && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
            <MapPin size={11} color="var(--text-muted)" />
            <span style={{ color: 'var(--text-secondary)' }}>{event.reason}</span>
          </div>
        )}
        <div style={{ display: 'flex', gap: 12, fontSize: 11, color: 'var(--text-muted)' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <CameraIcon size={10} /> {event.camera_id}
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <Clock size={10} /> {fmtTime(event.event_timestamp)}
          </span>
          {event.entity_id && (
            <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <PersonStanding size={10} /> {event.entity_id}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Detail modal ──────────────────────────────────────────────────────────────

function BehaviorDetailModal({ event, detail, onClose }: {
  event: AccessEvent
  detail: EventDetail | null
  onClose: () => void
}) {
  const meta = getBehaviorMeta(event.event_type)
  const imgUrl = detectImageUrl(event.image_path ?? detail?.image_path)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'rgba(0,0,0,.75)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: 20,
      }}
    >
      <div style={{
        background: 'var(--bg-elevated)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--r-xl)',
        width: '100%', maxWidth: 720,
        maxHeight: '90vh',
        overflow: 'auto',
        boxShadow: '0 24px 64px rgba(0,0,0,.6)',
      }}>
        {/* Modal header */}
        <div style={{
          padding: '14px 18px',
          borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', gap: 10,
          background: `${meta.color}12`,
          position: 'sticky', top: 0, zIndex: 1,
        }}>
          <span style={{ color: meta.color }}>{meta.icon}</span>
          <div>
            <div style={{ fontWeight: 700, fontSize: 14, color: meta.color }}>{meta.label}</div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{meta.desc}</div>
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <span style={{
              fontSize: 10, fontWeight: 800,
              color: SEV_COLOR[event.severity],
              background: `${SEV_COLOR[event.severity]}22`,
              border: `1px solid ${SEV_COLOR[event.severity]}`,
              borderRadius: 4, padding: '2px 8px',
            }}>{event.severity}</span>
            <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: 4 }}>
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Modal body */}
        <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Evidence image */}
          {imgUrl
            ? <img src={imgUrl} alt="evidence" onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                style={{ width: '100%', aspectRatio: '16/9', objectFit: 'cover', borderRadius: 'var(--r-md)', border: '1px solid var(--border)' }} />
            : <PlaceholderImage label="Chưa có ảnh bằng chứng" aspectRatio="16/9" />
          }

          {/* Metadata table */}
          <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', overflow: 'hidden' }}>
            {[
              { label: 'Loại sự kiện', value: meta.label, icon: meta.icon },
              { label: 'Camera', value: event.camera_id ?? '---', icon: <CameraIcon size={12} /> },
              { label: 'Thời điểm', value: fmtFull(event.event_timestamp), icon: <Clock size={12} /> },
              { label: 'Khu vực / Lý do', value: event.reason ?? '---', icon: <MapPin size={12} /> },
              ...(event.entity_id ? [{ label: 'Track ID', value: event.entity_id, icon: <PersonStanding size={12} /> }] : []),
            ].map((row, i, arr) => (
              <div key={row.label} style={{
                display: 'flex',
                padding: '9px 14px',
                borderBottom: i < arr.length - 1 ? '1px solid var(--border)' : 'none',
                alignItems: 'flex-start', gap: 6,
              }}>
                <div style={{ width: 130, fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
                  <span style={{ color: 'var(--text-muted)' }}>{row.icon}</span> {row.label}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-primary)' }}>{row.value}</div>
              </div>
            ))}
          </div>

          {/* Alert status */}
          <div style={{ display: 'flex', gap: 8 }}>
            {event.alert_sent && (
              <div style={{
                padding: '8px 12px',
                background: 'var(--warning-glow)',
                border: '1px solid var(--warning)',
                borderRadius: 'var(--r-md)',
                fontSize: 12, color: 'var(--warning)',
                display: 'flex', alignItems: 'center', gap: 6,
              }}>
                <AlertTriangle size={13} /> Cảnh báo đã được gửi
              </div>
            )}
          </div>

          {/* Raw metadata if available */}
          {detail?.metadata && Object.keys(detail.metadata).length > 0 && (
            <details>
              <summary style={{ fontSize: 11, color: 'var(--text-muted)', cursor: 'pointer' }}>AI metadata raw</summary>
              <pre style={{
                marginTop: 6, fontSize: 10,
                fontFamily: 'JetBrains Mono, monospace',
                color: 'var(--text-secondary)',
                background: 'var(--bg-surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--r-sm)',
                padding: '8px 10px',
                overflow: 'auto', maxHeight: 160,
                whiteSpace: 'pre-wrap',
              }}>
                {JSON.stringify(detail.metadata, null, 2)}
              </pre>
            </details>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Stats bar ─────────────────────────────────────────────────────────────────

function StatsBar({ events }: { events: AccessEvent[] }) {
  const bySev = events.reduce((acc, e) => { acc[e.severity] = (acc[e.severity] ?? 0) + 1; return acc }, {} as Record<string, number>)
  const byType = events.reduce((acc, e) => { acc[e.event_type] = (acc[e.event_type] ?? 0) + 1; return acc }, {} as Record<string, number>)

  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {Object.entries(SEV_COLOR).filter(([s]) => bySev[s]).map(([sev, color]) => (
        <div key={sev} style={{
          padding: '4px 10px',
          background: `${color}15`,
          border: `1px solid ${color}40`,
          borderRadius: 'var(--r-sm)',
          fontSize: 11, color,
          fontWeight: 600,
        }}>
          {bySev[sev]} {sev}
        </div>
      ))}
      {Object.entries(byType).map(([type, count]) => {
        const m = getBehaviorMeta(type)
        return (
          <div key={type} style={{
            padding: '4px 10px',
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--r-sm)',
            fontSize: 11, color: 'var(--text-muted)',
            display: 'flex', alignItems: 'center', gap: 5,
          }}>
            <span style={{ color: m.color }}>{m.icon}</span>
            {count} {m.label.split(' ')[0]}
          </div>
        )
      })}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function BehaviorPage() {
  const [events, setEvents] = useState<AccessEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [typeFilter, setTypeFilter] = useState('')
  const [severityFilter, setSeverityFilter] = useState('')
  const [cameraFilter, setCameraFilter] = useState('')
  const [selected, setSelected] = useState<AccessEvent | null>(null)
  const [detail, setDetail] = useState<EventDetail | null>(null)
  const [page, setPage] = useState(1)

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    else setRefreshing(true)
    try {
      if (isDevMode()) {
        await new Promise(r => setTimeout(r, 400))
        setEvents(MOCK_BEHAVIORS)
      } else {
        // Chỉ fetch các event_type AI core đã implement — placeholder
        // (loitering / zone_intrusion / crowd_gathering) không query tránh
        // spam BE trả list rỗng.
        const results = await Promise.allSettled(
          IMPLEMENTED_BEHAVIOR_TYPES.map(t => eventsApi.list({ event_type: t, limit: 100 }))
        )
        const all: AccessEvent[] = []
        for (const r of results) {
          if (r.status === 'fulfilled') all.push(...r.value)
        }
        all.sort((a, b) => new Date(b.event_timestamp).getTime() - new Date(a.event_timestamp).getTime())
        setEvents(all)
      }
    } catch (e) {
      console.error('BehaviorPage load error', e)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Load detail when modal opens
  useEffect(() => {
    if (!selected) { setDetail(null); return }
    if (isDevMode()) { setDetail(null); return }
    eventsApi.getDetail(selected.id)
      .then(setDetail)
      .catch(() => setDetail(null))
  }, [selected])

  const cameras = Array.from(new Set(events.map(e => e.camera_id).filter(Boolean))) as string[]

  const filtered = events.filter(e => {
    if (typeFilter && e.event_type !== typeFilter) return false
    if (severityFilter && e.severity !== severityFilter) return false
    if (cameraFilter && e.camera_id !== cameraFilter) return false
    return true
  })

  const pageData = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  // Reset page when filters change
  useEffect(() => { setPage(1) }, [typeFilter, severityFilter, cameraFilter])

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Page header */}
      <div style={{
        padding: '10px 14px',
        borderBottom: '1px solid var(--border)',
        background: 'var(--bg-elevated)',
        flexShrink: 0,
        display: 'flex', flexDirection: 'column', gap: 10,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Activity size={16} color="var(--warning)" />
          <span style={{ fontWeight: 700, fontSize: 14 }}>Nhận diện Hành vi</span>
          <span style={{
            fontSize: 11, color: 'var(--text-muted)',
            background: 'var(--bg-surface)', border: '1px solid var(--border)',
            borderRadius: 4, padding: '2px 6px',
          }}>{filtered.length} sự kiện</span>

          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
            {/* Type filter */}
            <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
              <Activity size={11} style={{ position: 'absolute', left: 7, color: 'var(--text-muted)', pointerEvents: 'none' }} />
              <select
                value={typeFilter}
                onChange={e => setTypeFilter(e.target.value)}
                style={{
                  paddingLeft: 24, paddingRight: 8, height: 30,
                  background: 'var(--bg-surface)', border: '1px solid var(--border)',
                  borderRadius: 'var(--r-sm)', color: typeFilter ? 'var(--text-primary)' : 'var(--text-muted)',
                  fontSize: 12, outline: 'none', cursor: 'pointer',
                }}
              >
                <option value="">Tất cả hành vi</option>
                <optgroup label="Đang hoạt động">
                  {IMPLEMENTED_BEHAVIOR_TYPES.map(t => (
                    <option key={t} value={t}>{BEHAVIOR_META[t].label}</option>
                  ))}
                </optgroup>
                <optgroup label="Sắp ra mắt">
                  {BEHAVIOR_TYPES.filter(t => !BEHAVIOR_META[t].implemented).map(t => (
                    <option key={t} value={t} disabled>{BEHAVIOR_META[t].label} (coming soon)</option>
                  ))}
                </optgroup>
              </select>
            </div>

            {/* Severity filter */}
            <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
              <AlertTriangle size={11} style={{ position: 'absolute', left: 7, color: 'var(--text-muted)', pointerEvents: 'none' }} />
              <select
                value={severityFilter}
                onChange={e => setSeverityFilter(e.target.value)}
                style={{
                  paddingLeft: 24, paddingRight: 8, height: 30,
                  background: 'var(--bg-surface)', border: '1px solid var(--border)',
                  borderRadius: 'var(--r-sm)', color: severityFilter ? SEV_COLOR[severityFilter] : 'var(--text-muted)',
                  fontSize: 12, outline: 'none', cursor: 'pointer',
                }}
              >
                <option value="">Mức độ</option>
                {['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>

            {/* Camera filter */}
            <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
              <SlidersHorizontal size={11} style={{ position: 'absolute', left: 7, color: 'var(--text-muted)', pointerEvents: 'none' }} />
              <select
                value={cameraFilter}
                onChange={e => setCameraFilter(e.target.value)}
                style={{
                  paddingLeft: 24, paddingRight: 8, height: 30,
                  background: 'var(--bg-surface)', border: '1px solid var(--border)',
                  borderRadius: 'var(--r-sm)', color: cameraFilter ? 'var(--text-primary)' : 'var(--text-muted)',
                  fontSize: 12, outline: 'none', cursor: 'pointer',
                }}
              >
                <option value="">Camera</option>
                {cameras.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>

            {/* Refresh */}
            <button
              onClick={() => load(true)}
              disabled={refreshing}
              style={{
                height: 30, width: 30,
                background: 'var(--bg-surface)', border: '1px solid var(--border)',
                borderRadius: 'var(--r-sm)', cursor: 'pointer', color: 'var(--text-muted)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
            >
              <RefreshCw size={12} style={{ animation: refreshing ? 'spin 1s linear infinite' : 'none' }} />
            </button>
          </div>
        </div>

        {/* Stats summary */}
        {!loading && events.length > 0 && <StatsBar events={filtered} />}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: 'auto', padding: 14 }}>
        {loading ? (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
            gap: 14,
          }}>
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} style={{
                background: 'var(--bg-elevated)', border: '1px solid var(--border)',
                borderRadius: 'var(--r-lg)', overflow: 'hidden',
              }}>
                <div style={{ height: 36, background: 'var(--bg-surface)' }} />
                <div style={{ padding: 12 }}>
                  <div style={{ aspectRatio: '16/9', background: 'var(--border)', borderRadius: 'var(--r-sm)', marginBottom: 10 }} />
                  <div style={{ height: 11, background: 'var(--border)', borderRadius: 4, marginBottom: 6, width: '80%' }} />
                  <div style={{ height: 10, background: 'var(--bg-surface)', borderRadius: 4, width: '55%' }} />
                </div>
              </div>
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 300, gap: 12, color: 'var(--text-muted)' }}>
            <Activity size={40} strokeWidth={1} />
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 14, marginBottom: 4 }}>
                {typeFilter || severityFilter || cameraFilter ? 'Không có kết quả phù hợp' : 'Chưa có sự kiện hành vi'}
              </div>
              <div style={{ fontSize: 12 }}>
                {typeFilter || severityFilter || cameraFilter
                  ? 'Thử bỏ bớt bộ lọc để xem thêm dữ liệu'
                  : 'AI sẽ tự động phát hiện khi pipeline xử lý video'
                }
              </div>
            </div>
          </div>
        ) : (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14 }}>
              {pageData.map(ev => (
                <BehaviorCard key={ev.id} event={ev} onClick={() => setSelected(ev)} />
              ))}
            </div>
            <Pagination page={page} total={filtered.length} pageSize={PAGE_SIZE} onChange={setPage} />
          </>
        )}
      </div>

      {/* Detail modal */}
      {selected && (
        <BehaviorDetailModal
          event={selected}
          detail={detail}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}
