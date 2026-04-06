import { useState, useEffect } from 'react'
import { eventsApi, type AccessEvent } from '../api'

/**
 * Trang Lịch sử cảnh báo (Events) — danh sách đầy đủ access_events
 * với bộ lọc theo severity, camera_id, event_type và phân trang.
 */
export default function EventsPage() {
  const [events, setEvents]     = useState<AccessEvent[]>([])
  const [loading, setLoading]   = useState(true)
  const [severity, setSeverity] = useState('')
  const [eventType, setEventType] = useState('')
  const [page, setPage]         = useState(0)
  const PAGE_SIZE = 30

  const load = async () => {
    setLoading(true)
    try {
      const data = await eventsApi.list({
        severity:   severity || undefined,
        event_type: eventType || undefined,
        limit:      PAGE_SIZE,
        offset:     page * PAGE_SIZE,
      })
      setEvents(data)
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [severity, eventType, page])

  const SEV_BADGE: Record<string, string> = {
    CRITICAL: 'critical', HIGH: 'high', MEDIUM: 'medium', LOW: 'low',
  }

  const EVENT_ICONS: Record<string, string> = {
    blacklist_person: '🚫',
    blacklist_vehicle: '🚗',
    zone_denied: '🔒',
    time_denied: '🕐',
    spoof_detected: '🎭',
    object_linked: '🔗',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header */}
      <div>
        <h2 style={{ fontSize: 18, fontWeight: 700 }}>📋 Lịch sử Cảnh báo</h2>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
          Toàn bộ sự kiện cảnh báo từ pipeline AI
        </p>
      </div>

      {/* Filters */}
      <div className="card" style={{ padding: '14px 16px' }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <label style={{ marginBottom: 0, whiteSpace: 'nowrap' }}>Mức độ:</label>
            <select className="input" style={{ width: 'auto' }}
              value={severity} onChange={e => { setSeverity(e.target.value); setPage(0) }}>
              <option value="">Tất cả</option>
              <option value="CRITICAL">💀 Critical</option>
              <option value="HIGH">🔴 High</option>
              <option value="MEDIUM">🟠 Medium</option>
              <option value="LOW">🟢 Low</option>
            </select>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <label style={{ marginBottom: 0, whiteSpace: 'nowrap' }}>Loại:</label>
            <select className="input" style={{ width: 'auto' }}
              value={eventType} onChange={e => { setEventType(e.target.value); setPage(0) }}>
              <option value="">Tất cả</option>
              <option value="blacklist_person">Blacklist người</option>
              <option value="blacklist_vehicle">Blacklist xe</option>
              <option value="zone_denied">Vi phạm zone</option>
              <option value="spoof_detected">Giả mạo</option>
              <option value="object_linked">Liên kết xe-người</option>
            </select>
          </div>
          <button className="btn btn--ghost btn--sm" onClick={load}>🔄 Tải lại</button>
          <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-muted)' }}>
            Trang {page + 1}
          </span>
        </div>
      </div>

      {/* Table */}
      <div className="card" style={{ padding: 0 }}>
        <div className="table-wrap">
          {loading ? (
            <div className="empty-state"><div className="empty-state__icon">⏳</div>Đang tải...</div>
          ) : events.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state__icon">🛡️</div>
              Không có sự kiện nào với bộ lọc hiện tại
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Mức độ</th>
                  <th>Loại sự kiện</th>
                  <th>Đối tượng</th>
                  <th>Camera</th>
                  <th>Lý do</th>
                  <th>Thời gian</th>
                  <th>Alert</th>
                </tr>
              </thead>
              <tbody>
                {events.map(evt => (
                  <tr key={evt.id}>
                    <td className="font-mono text-muted">#{evt.id}</td>
                    <td>
                      <span className={`badge badge--${SEV_BADGE[evt.severity] ?? 'info'}`}>
                        {evt.severity}
                      </span>
                    </td>
                    <td>
                      {EVENT_ICONS[evt.event_type] ?? '⚡'}{' '}
                      {evt.event_type.replace(/_/g, ' ')}
                    </td>
                    <td>
                      <div style={{ fontWeight: 600, fontSize: 12 }}>{evt.entity_id ?? '—'}</div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{evt.entity_type}</div>
                    </td>
                    <td className="font-mono text-sm">{evt.camera_id ?? '—'}</td>
                    <td style={{ maxWidth: 200 }}>
                      <div className="truncate text-muted text-sm">{evt.reason ?? '—'}</div>
                    </td>
                    <td className="font-mono text-sm" style={{ whiteSpace: 'nowrap' }}>
                      {new Date(evt.event_timestamp).toLocaleString('vi-VN')}
                    </td>
                    <td>
                      {evt.alert_sent
                        ? <span className="badge badge--success">✅ Đã gửi</span>
                        : <span className="badge badge--medium">⏳ Chưa</span>
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Pagination */}
      <div style={{ display: 'flex', justifyContent: 'center', gap: 8 }}>
        <button className="btn btn--ghost btn--sm" disabled={page === 0} onClick={() => setPage(p => p - 1)}>
          ← Trang trước
        </button>
        <button className="btn btn--ghost btn--sm"
          disabled={events.length < PAGE_SIZE} onClick={() => setPage(p => p + 1)}>
          Trang sau →
        </button>
      </div>
    </div>
  )
}
