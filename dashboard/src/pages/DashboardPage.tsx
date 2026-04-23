import { useEffect, useRef, useState } from 'react'
import useSWR from 'swr'
import {
  camerasApi, eventsApi, detectImageUrl,
  type AccessEvent,
} from '../api'
import {
  Activity, AlertOctagon, AlertCircle, AlertTriangle, CheckCircle,
  Camera as CameraIcon, ShieldCheck, RefreshCw, Clock, Video, UserX, User as UserIcon, Car,
} from 'lucide-react'
import { CameraGrid } from '../components/CameraStream'

const SEV_COLOR: Record<string, string> = {
  CRITICAL: 'critical', HIGH: 'high', MEDIUM: 'medium', LOW: 'low',
}

const SEV_ICON: Record<string, React.ElementType> = {
  CRITICAL: AlertOctagon, HIGH: AlertCircle, MEDIUM: AlertTriangle, LOW: CheckCircle,
}

function eventEntityIcon(ev: AccessEvent) {
  if (ev.entity_type === 'plate') return Car
  if (ev.event_type === 'stranger_detected') return UserX
  return UserIcon
}

function eventLabel(ev: AccessEvent): string {
  switch (ev.event_type) {
    case 'stranger_detected': return 'Người lạ'
    case 'face_recognition':  return 'Nhận diện mặt'
    case 'lpr_recognition':   return 'Biển số xe'
    case 'blacklist_person':  return 'Cảnh báo: Blacklist Người'
    case 'blacklist_vehicle': return 'Cảnh báo: Blacklist Xe'
    default: return ev.event_type.replace(/_/g, ' ')
  }
}

function fmtTime(ts: string): string {
  try { return new Date(ts).toLocaleTimeString('vi-VN', { hour12: false }) }
  catch { return ts }
}

function fmtDate(ts: string): string {
  try { return new Date(ts).toLocaleDateString('vi-VN') }
  catch { return '' }
}

// ── Event Feed Item ───────────────────────────────────────────────────────────

function EventFeedItem({ ev }: { ev: AccessEvent }) {
  const Icon  = eventEntityIcon(ev)
  const SevIcon = SEV_ICON[ev.severity] ?? AlertTriangle
  const sevCls = SEV_COLOR[ev.severity] ?? 'low'
  const imgUrl = detectImageUrl(ev.image_path)

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '64px 1fr',
        gap: 10,
        padding: 10,
        borderRadius: 'var(--r-md)',
        background: 'var(--bg-elevated)',
        borderLeft: `3px solid var(--sev-${sevCls})`,
      }}
    >
      {/* Thumbnail */}
      <div
        style={{
          width: 64, height: 64,
          borderRadius: 'var(--r-sm)',
          background: '#000',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          overflow: 'hidden', flexShrink: 0,
        }}
      >
        {imgUrl ? (
          <img
            src={imgUrl}
            alt={ev.entity_id ?? 'detection'}
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        ) : (
          <Icon size={28} style={{ color: 'var(--text-muted)' }} />
        )}
      </div>

      {/* Body */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <span className={`badge badge--${sevCls}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
            <SevIcon size={10} /> {ev.severity}
          </span>
          <span style={{ fontSize: 12, fontWeight: 600 }}>
            <Icon size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 3 }} />
            {eventLabel(ev)}
          </span>
          <span className="font-mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            {ev.entity_id ?? '—'}
          </span>
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <span><CameraIcon size={10} style={{ display: 'inline', verticalAlign: '-1px' }} /> {ev.camera_id ?? '?'}</span>
          {ev.reason && <span>· {ev.reason}</span>}
          <span style={{ marginLeft: 'auto' }} className="font-mono">
            {fmtTime(ev.event_timestamp)}
            <span style={{ opacity: 0.6 }}> {fmtDate(ev.event_timestamp)}</span>
          </span>
        </div>
      </div>
    </div>
  )
}

// ── Dashboard Page ────────────────────────────────────────────────────────────

export default function DashboardPage() {
  // Stats — refresh 15s
  const { data: stats } = useSWR(
    '/api/events/stats', eventsApi.stats, { refreshInterval: 15000 }
  )

  // Cameras — refresh 30s
  const { data: cameras = [], isLoading: loadingCams } = useSWR(
    '/api/cameras', camerasApi.list, { refreshInterval: 30000 }
  )

  // Events feed — refresh 5s
  const { data: events = [], isLoading: loadingEvents, mutate: mutateEvents } = useSWR(
    '/api/events?limit=50',
    () => eventsApi.list({ limit: 50 }),
    { refreshInterval: 5000 }
  )

  // Highlight new events with subtle glow when they arrive
  const lastIdsRef = useRef<Set<string>>(new Set())
  const [newIds, setNewIds] = useState<Set<string>>(new Set())
  useEffect(() => {
    const fresh = new Set<string>()
    for (const e of events) {
      if (lastIdsRef.current.size > 0 && !lastIdsRef.current.has(e.id)) fresh.add(e.id)
    }
    lastIdsRef.current = new Set(events.map(e => e.id))
    if (fresh.size > 0) {
      setNewIds(fresh)
      const t = setTimeout(() => setNewIds(new Set()), 3000)
      return () => clearTimeout(t)
    }
  }, [events])

  const bySev = stats?.by_severity ?? {}

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Compact stat strip */}
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
              <div className="stat-card__value" style={{ color: `var(--sev-${cls})` }}>
                {bySev[sev] ?? 0}
              </div>
            </div>
          )
        })}
      </div>

      {/* 2-column layout: Live cam (left) + Detection feed (right) */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.6fr) minmax(0, 1fr)',
          gap: 16,
          alignItems: 'stretch',
        }}
      >
        {/* LEFT — Live cameras */}
        <div className="card" style={{ display: 'flex', flexDirection: 'column' }}>
          <div className="card__title">
            <Video size={16} /> Camera trực tiếp
            <span className="text-muted" style={{ fontSize: 11, marginLeft: 'auto' }}>
              {cameras.filter(c => c.enabled).length}/{cameras.length} online
            </span>
          </div>
          {loadingCams ? (
            <div className="empty-state">
              <Clock size={36} className="empty-state__icon" /> Đang tải camera...
            </div>
          ) : (
            <CameraGrid cameras={cameras} />
          )}
        </div>

        {/* RIGHT — Detection feed (live, auto-refresh) */}
        <div className="card" style={{ display: 'flex', flexDirection: 'column', minHeight: 600 }}>
          <div className="card__title" style={{ justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Activity size={16} /> Log AI Detect
              <span className="dot-live" style={{ width: 8, height: 8 }} />
            </div>
            <button className="btn btn--ghost btn--sm" onClick={() => mutateEvents()}>
              <RefreshCw size={12} className={loadingEvents ? 'animate-spin' : ''} />
            </button>
          </div>

          <div
            style={{
              flex: 1,
              overflowY: 'auto',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              paddingRight: 4,
              maxHeight: 'calc(100vh - 320px)',
            }}
          >
            {loadingEvents && events.length === 0 ? (
              <div className="empty-state">
                <Clock size={36} className="empty-state__icon" /> Đang chờ event...
              </div>
            ) : events.length === 0 ? (
              <div className="empty-state">
                <ShieldCheck size={36} className="empty-state__icon" style={{ color: 'var(--success)' }} />
                Chưa có detection nào
              </div>
            ) : (
              events.map(ev => (
                <div
                  key={ev.id}
                  style={{
                    transition: 'box-shadow 0.6s',
                    boxShadow: newIds.has(ev.id)
                      ? '0 0 0 2px var(--brand), 0 0 20px var(--brand)40'
                      : 'none',
                    borderRadius: 'var(--r-md)',
                  }}
                >
                  <EventFeedItem ev={ev} />
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
