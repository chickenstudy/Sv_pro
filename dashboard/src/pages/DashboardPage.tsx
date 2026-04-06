import { useState, useEffect, useCallback } from 'react'
import { eventsApi, type EventStats, type AccessEvent } from '../api'

const SEV_COLOR: Record<string, string> = {
  CRITICAL: 'critical', HIGH: 'high', MEDIUM: 'medium', LOW: 'low',
}

/**
 * Trang Dashboard chính — hiển thị:
 *  - 4 stat cards: Tổng alert hôm nay, HIGH/CRITICAL, Medium, Low
 *  - Bar chart: phân bố theo mức độ nghiêm trọng
 *  - Alert feed: 20 event mới nhất, tự refresh mỗi 15 giây
 *  - Top 5 cameras kích hoạt cảnh báo nhiều nhất hôm nay
 */
export default function DashboardPage() {
  const [stats, setStats]   = useState<EventStats | null>(null)
  const [events, setEvents] = useState<AccessEvent[]>([])
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const [s, e] = await Promise.all([
        eventsApi.stats(),
        eventsApi.list({ limit: 20 }),
      ])
      setStats(s)
      setEvents(e)
    } catch {
      // silent — lỗi kết nối không crash UI
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 15_000)
    return () => clearInterval(id)
  }, [refresh])

  const bySev = stats?.by_severity ?? {}
  const maxSev = Math.max(1, ...Object.values(bySev))

  const SEV_LABELS: [string, string, string][] = [
    ['CRITICAL', '💀', 'critical'],
    ['HIGH', '🔴', 'high'],
    ['MEDIUM', '🟠', 'medium'],
    ['LOW', '🟢', 'low'],
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* Stat cards */}
      <div className="stats-grid">
        <div className="stat-card" style={{ '--card-accent': 'var(--brand)' } as any}>
          <span className="stat-card__icon">📊</span>
          <div className="stat-card__label">Tổng Alert Hôm Nay</div>
          <div className="stat-card__value">{loading ? '—' : stats?.total ?? 0}</div>
          <div className="stat-card__sub">{stats?.date ?? '...'}</div>
        </div>

        {SEV_LABELS.slice(0, 2).map(([sev, icon, cls]) => (
          <div key={sev} className="stat-card" style={{ '--card-accent': `var(--sev-${cls})` } as any}>
            <span className="stat-card__icon">{icon}</span>
            <div className="stat-card__label">{sev}</div>
            <div className="stat-card__value" style={{ color: `var(--sev-${cls})` }}>
              {loading ? '—' : bySev[sev] ?? 0}
            </div>
          </div>
        ))}

        <div className="stat-card" style={{ '--card-accent': 'var(--success)' } as any}>
          <span className="stat-card__icon">✅</span>
          <div className="stat-card__label">LOW / MEDIUM</div>
          <div className="stat-card__value" style={{ color: 'var(--success)' }}>
            {loading ? '—' : (bySev['LOW'] ?? 0) + (bySev['MEDIUM'] ?? 0)}
          </div>
        </div>
      </div>

      {/* Charts + Top cameras */}
      <div className="grid-2">
        {/* Bar chart */}
        <div className="card">
          <div className="card__title">📈 Phân bố theo mức độ</div>
          <div className="chart-area" style={{ minHeight: 150 }}>
            {SEV_LABELS.map(([sev, , cls]) => (
              <div
                key={sev}
                className="chart-bar"
                title={`${sev}: ${bySev[sev] ?? 0}`}
                style={{
                  height: `${Math.max(4, ((bySev[sev] ?? 0) / maxSev) * 120)}px`,
                  background: `linear-gradient(to top, var(--sev-${cls}), var(--sev-${cls})80)`,
                }}
              />
            ))}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-around', marginTop: 8 }}>
            {SEV_LABELS.map(([sev, icon]) => (
              <span key={sev} style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                {icon} {sev.slice(0, 3)}
              </span>
            ))}
          </div>
        </div>

        {/* Top cameras */}
        <div className="card">
          <div className="card__title">📷 Top Camera Cảnh Báo</div>
          {loading ? (
            <div className="empty-state"><div className="empty-state__icon">⏳</div>Đang tải...</div>
          ) : (stats?.top_cameras?.length ?? 0) === 0 ? (
            <div className="empty-state"><div className="empty-state__icon">📷</div>Chưa có dữ liệu hôm nay</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {stats!.top_cameras.map((c, i) => (
                <div key={c.camera_id} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)', width: 16, textAlign: 'right' }}>
                    #{i + 1}
                  </span>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>{c.camera_id}</div>
                    <div style={{
                      height: 4, borderRadius: 2,
                      background: `linear-gradient(to right, var(--brand), var(--accent))`,
                      width: `${(c.count / (stats!.top_cameras[0]?.count || 1)) * 100}%`,
                      marginTop: 3,
                    }} />
                  </div>
                  <span className="badge badge--brand font-mono">{c.count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Alert feed */}
      <div className="card">
        <div className="card__title" style={{ justifyContent: 'space-between' }}>
          <span>🚨 Cảnh báo gần nhất</span>
          <button className="btn btn--ghost btn--sm" onClick={refresh}>🔄 Refresh</button>
        </div>
        {loading ? (
          <div className="empty-state"><div className="empty-state__icon">⏳</div>Đang tải...</div>
        ) : events.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state__icon">🛡️</div>
            Không có cảnh báo hôm nay — hệ thống an toàn
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {events.map(evt => (
              <div key={evt.id} className="alert-item">
                <div className={`alert-item__dot alert-item__dot--${evt.severity}`} />
                <div className="alert-item__body">
                  <div className="alert-item__title">
                    <span className={`badge badge--${SEV_COLOR[evt.severity] ?? 'info'}`}>
                      {evt.severity}
                    </span>
                    {' '}
                    {evt.event_type.replace(/_/g, ' ')} — {evt.entity_id ?? 'N/A'}
                  </div>
                  <div className="alert-item__meta">
                    📷 {evt.camera_id ?? '?'} &nbsp;|&nbsp;
                    {evt.reason ?? 'Không có mô tả'} &nbsp;|&nbsp;
                    <span className="font-mono">{new Date(evt.event_timestamp).toLocaleString('vi-VN')}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
