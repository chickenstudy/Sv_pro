import { useState, useEffect } from 'react'
import { eventsApi, type AccessEvent } from '../api'

// ── Hằng số UI ─────────────────────────────────────────────────────────────────
const SEVERITY_COLORS: Record<string, string> = {
  LOW:      'var(--success)',
  MEDIUM:   'var(--warning)',
  HIGH:     'var(--danger)',
  CRITICAL: '#ff00aa',
}

const SEVERITY_LABELS: Record<string, string> = {
  LOW:      '🟢 Thấp',
  MEDIUM:   '🟡 Trung bình',
  HIGH:     '🟠 Cao',
  CRITICAL: '🔴 Nghiêm trọng',
}

// ── Loại sự kiện hiển thị dễ đọc ───────────────────────────────────────────────
const EVENT_TYPE_LABELS: Record<string, string> = {
  blacklist_plate:   '🚗 Biển số đen',
  blacklist_person:  '🚫 Người đen',
  stranger_detected: '👤 Người lạ',
  door_trigger:      '🚪 Mở cửa',
  zone_violation:    '⛔ Vi phạm vùng',
  spoof_attempt:     '🎭 Giả mạo mặt',
}

/**
 * AlertsPage — Trang lịch sử cảnh báo SV-PRO.
 * Hiển thị danh sách access_events với bộ lọc severity, loại sự kiện, camera.
 * Load thêm bằng nút "Tải thêm". Tự refresh mỗi 30 giây.
 */
export default function AlertsPage() {
  const [events,     setEvents]     = useState<AccessEvent[]>([])
  const [loading,    setLoading]    = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error,      setError]      = useState<string | null>(null)
  const [offset,     setOffset]     = useState(0)
  const [hasMore,    setHasMore]    = useState(true)

  // Bộ lọc
  const [filterSeverity,  setFilterSeverity]  = useState('')
  const [filterEventType, setFilterEventType] = useState('')
  const [filterCamera,    setFilterCamera]    = useState('')

  const PAGE_SIZE = 30

  // Hàm tải dữ liệu sự kiện
  const loadEvents = async (reset = false) => {
    try {
      reset ? setLoading(true) : setLoadingMore(true)
      const currentOffset = reset ? 0 : offset
      const data = await eventsApi.list({
        severity:   filterSeverity   || undefined,
        event_type: filterEventType  || undefined,
        camera_id:  filterCamera     || undefined,
        limit:      PAGE_SIZE,
        offset:     currentOffset,
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

  // Load lần đầu và auto-refresh 30 giây
  useEffect(() => {
    loadEvents(true)
    const id = setInterval(() => loadEvents(true), 30_000)
    return () => clearInterval(id)
  }, [filterSeverity, filterEventType, filterCamera])

  // Format timestamp sang giờ VN
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
      <div className="page-header">
        <h2 style={{ margin: 0 }}>🔔 Lịch sử cảnh báo</h2>
        <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>
          Tự động làm mới mỗi 30 giây
        </span>
      </div>

      {/* ── Bộ lọc ──────────────────────────────────────────────────────────── */}
      <div className="filter-bar" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '16px 0' }}>
        <select
          className="form-select"
          value={filterSeverity}
          onChange={e => setFilterSeverity(e.target.value)}
          style={{ minWidth: 160 }}
        >
          <option value="">📊 Tất cả mức độ</option>
          <option value="LOW">🟢 Thấp</option>
          <option value="MEDIUM">🟡 Trung bình</option>
          <option value="HIGH">🟠 Cao</option>
          <option value="CRITICAL">🔴 Nghiêm trọng</option>
        </select>

        <select
          className="form-select"
          value={filterEventType}
          onChange={e => setFilterEventType(e.target.value)}
          style={{ minWidth: 180 }}
        >
          <option value="">🏷️ Tất cả loại sự kiện</option>
          {Object.entries(EVENT_TYPE_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>

        <input
          type="text"
          className="form-input"
          placeholder="🎥 Lọc theo Camera ID..."
          value={filterCamera}
          onChange={e => setFilterCamera(e.target.value)}
          style={{ minWidth: 200 }}
        />

        <button
          className="btn btn-secondary"
          onClick={() => loadEvents(true)}
          disabled={loading}
        >
          🔄 Làm mới
        </button>
      </div>

      {/* ── Danh sách sự kiện ────────────────────────────────────────────────── */}
      {loading ? (
        <div className="loading-state">
          <div className="spinner" />
          <p>Đang tải dữ liệu cảnh báo...</p>
        </div>
      ) : error ? (
        <div className="error-banner">
          ⚠️ {error}
          <button className="btn btn-sm" onClick={() => loadEvents(true)}>Thử lại</button>
        </div>
      ) : events.length === 0 ? (
        <div className="empty-state">
          <div style={{ fontSize: 48, opacity: 0.3 }}>🔕</div>
          <p>Không có cảnh báo nào phù hợp với bộ lọc.</p>
        </div>
      ) : (
        <div className="alerts-list">
          {events.map(evt => (
            <AlertCard key={evt.id} event={evt}
              formatTime={formatTime}
              severityColors={SEVERITY_COLORS}
              severityLabels={SEVERITY_LABELS}
              eventTypeLabels={EVENT_TYPE_LABELS}
            />
          ))}

          {hasMore && (
            <div style={{ textAlign: 'center', padding: 16 }}>
              <button
                className="btn btn-secondary"
                onClick={() => loadEvents(false)}
                disabled={loadingMore}
              >
                {loadingMore ? '⏳ Đang tải...' : '📥 Tải thêm'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── AlertCard component ──────────────────────────────────────────────────────

interface AlertCardProps {
  event:            AccessEvent
  formatTime:       (iso: string) => string
  severityColors:   Record<string, string>
  severityLabels:   Record<string, string>
  eventTypeLabels:  Record<string, string>
}

/** Thẻ hiển thị một sự kiện cảnh báo với màu sắc và thông tin chi tiết. */
function AlertCard({ event, formatTime, severityColors, severityLabels, eventTypeLabels }: AlertCardProps) {
  const [expanded, setExpanded] = useState(false)
  const color = severityColors[event.severity] || 'var(--text-muted)'

  return (
    <div
      className="alert-card"
      style={{ borderLeft: `4px solid ${color}`, cursor: 'pointer' }}
      onClick={() => setExpanded(e => !e)}
    >
      <div className="alert-card-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ color, fontWeight: 700, fontSize: 13 }}>
            {severityLabels[event.severity] || event.severity}
          </span>
          <span className="badge badge-type">
            {eventTypeLabels[event.event_type] || event.event_type}
          </span>
          {event.alert_sent && (
            <span className="badge badge-sent" title="Đã gửi Telegram/Webhook">
              📨 Đã gửi
            </span>
          )}
        </div>
        <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
          {formatTime(event.event_timestamp)}
        </span>
      </div>

      <div className="alert-card-body">
        {event.entity_id && (
          <span className="data-chip">
            🆔 {event.entity_type === 'person' ? '👤' : '🚗'} {event.entity_id}
          </span>
        )}
        {event.camera_id && (
          <span className="data-chip">🎥 {event.camera_id}</span>
        )}
        {event.reason && (
          <span className="data-chip reason">📝 {event.reason}</span>
        )}
      </div>

      {expanded && event.source_id && (
        <div className="alert-card-detail">
          <div className="detail-row">
            <span className="detail-label">Source ID</span>
            <code>{event.source_id}</code>
          </div>
          <div className="detail-row">
            <span className="detail-label">Event ID</span>
            <code>#{event.id}</code>
          </div>
        </div>
      )}
    </div>
  )
}
