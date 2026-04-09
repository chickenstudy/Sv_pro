import { useState, useEffect } from 'react'
import { BellRing, ShieldAlert, Activity, CheckCircle, Video, ShieldCheck, Clock, RefreshCw, EyeOff, Search } from 'lucide-react'
import { eventsApi, type AccessEvent } from '../api'

const SEVERITY_COLORS: Record<string, string> = {
  LOW: 'var(--success)',
  MEDIUM: 'var(--warning)',
  HIGH: 'var(--danger)',
  CRITICAL: '#ff00aa',
}

const SEVERITY_ICONS: Record<string, any> = {
  LOW: CheckCircle,
  MEDIUM: Activity,
  HIGH: ShieldAlert,
  CRITICAL: ShieldAlert,
}

const EVENT_ICONS: Record<string, any> = {
  blacklist_plate: ShieldAlert,
  blacklist_person: ShieldAlert,
  stranger_detected: EyeOff,
  door_trigger: Activity,
  zone_violation: Activity,
  spoof_attempt: EyeOff,
}

export default function AlertsPage() {
  const [events, setEvents] = useState<AccessEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(true)

  const [filterSeverity, setFilterSeverity] = useState('')
  const [filterEventType, setFilterEventType] = useState('')
  const [filterCamera, setFilterCamera] = useState('')

  const PAGE_SIZE = 30

  const loadEvents = async (reset = false) => {
    try {
      reset ? setLoading(true) : setLoadingMore(true)
      const currentOffset = reset ? 0 : offset
      const data = await eventsApi.list({
        severity: filterSeverity || undefined,
        event_type: filterEventType || undefined,
        camera_id: filterCamera || undefined,
        limit: PAGE_SIZE,
        offset: currentOffset,
      })
      if (reset) {
        setEvents(data)
        setOffset(PAGE_SIZE)
      } else {
        setEvents(prev => [...prev, ...data])
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
    loadEvents(true)
    const id = setInterval(() => loadEvents(true), 30_000)
    return () => clearInterval(id)
  }, [filterSeverity, filterEventType, filterCamera])

  const formatTime = (iso: string) => {
    try {
      return new Date(iso).toLocaleString('vi-VN', {
        day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
      })
    } catch { return iso }
  }

  return (
    <div className="alerts-page">
      <div className="page-header" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <BellRing size={28} color="var(--brand)" />
        <div>
          <h2 style={{ margin: 0 }}>Lịch sử cảnh báo</h2>
          <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>Tự động làm mới mỗi 30 giây</span>
        </div>
      </div>

      <div className="filter-bar card" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '16px 0', padding: 14 }}>
        <select className="form-select" value={filterSeverity} onChange={e => setFilterSeverity(e.target.value)}>
          <option value="">Tất cả mức độ</option>
          <option value="LOW">Thấp</option>
          <option value="MEDIUM">Trung bình</option>
          <option value="HIGH">Cao</option>
          <option value="CRITICAL">Nghiêm trọng</option>
        </select>

        <select className="form-select" value={filterEventType} onChange={e => setFilterEventType(e.target.value)}>
          <option value="">Tất cả loại sự kiện</option>
          <option value="blacklist_plate">Biển số đen</option>
          <option value="blacklist_person">Người đen</option>
          <option value="stranger_detected">Người lạ</option>
          <option value="door_trigger">Mở cửa</option>
          <option value="zone_violation">Vi phạm vùng</option>
          <option value="spoof_attempt">Giả mạo mặt</option>
        </select>

        <input type="text" className="form-input" placeholder="Lọc theo Camera ID..."
          value={filterCamera} onChange={e => setFilterCamera(e.target.value)} />

        <button className="btn btn--primary" onClick={() => loadEvents(true)} disabled={loading}>
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Làm mới
        </button>
      </div>

      {loading && events.length === 0 ? (
        <div className="loading-state empty-state"><Clock size={36} className="empty-state__icon animate-spin" /> Đang tải...</div>
      ) : error ? (
        <div className="error-banner">⚠️ {error} <button className="btn btn--sm" onClick={() => loadEvents(true)}>Thử lại</button></div>
      ) : events.length === 0 ? (
        <div className="empty-state"><ShieldCheck size={48} className="empty-state__icon" style={{ color: 'var(--success)' }} /> Không có cảnh báo nào phù hợp với bộ lọc.</div>
      ) : (
        <div className="alerts-list">
          {events.map(evt => {
            const color = SEVERITY_COLORS[evt.severity] || 'var(--text-muted)'
            const SevIcon = SEVERITY_ICONS[evt.severity] || Activity
            const EventIcon = EVENT_ICONS[evt.event_type] || Activity

            return (
              <div key={evt.id} className="alert-card" style={{ borderLeft: `4px solid ${color}` }}>
                <div className="alert-card-header">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span style={{ color, fontWeight: 700, fontSize: 13, display: 'flex', alignItems: 'center', gap: 4 }}>
                      <SevIcon size={14} /> {evt.severity}
                    </span>
                    <span className="badge badge-type" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <EventIcon size={12} /> {evt.event_type}
                    </span>
                  </div>
                  <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{formatTime(evt.event_timestamp)}</span>
                </div>

                <div className="alert-card-body mt-2">
                  {evt.entity_id && (
                    <span className="data-chip inline-flex items-center gap-1">
                      <Search size={12} /> {evt.entity_type === 'person' ? 'User' : 'Vehicle'}: {evt.entity_id}
                    </span>
                  )}
                  {evt.camera_id && (
                    <span className="data-chip inline-flex items-center gap-1"><Video size={12} /> {evt.camera_id}</span>
                  )}
                  {evt.reason && (
                    <span className="data-chip reason inline-flex items-center gap-1"><Activity size={12} /> {evt.reason}</span>
                  )}
                </div>
              </div>
            )
          })}

          {hasMore && (
            <div style={{ textAlign: 'center', padding: 16 }}>
              <button className="btn btn--secondary" onClick={() => loadEvents(false)} disabled={loadingMore}>
                {loadingMore ? 'Đang tải...' : 'Tải thêm'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
