import useSWR from 'swr'
import { camerasApi, eventsApi } from '../api'
import {
  Activity,
  AlertOctagon,
  AlertCircle,
  AlertTriangle,
  CheckCircle,
  BarChart3,
  Camera as CameraIcon,
  ShieldCheck,
  RefreshCw,
  Clock,
  Video,
} from 'lucide-react'
import { CameraGrid } from '../components/CameraStream'

const SEV_COLOR: Record<string, string> = {
  CRITICAL: 'critical', HIGH: 'high', MEDIUM: 'medium', LOW: 'low',
}

const SEV_LABELS = [
  { id: 'CRITICAL', icon: AlertOctagon, cls: 'critical' },
  { id: 'HIGH', icon: AlertCircle, cls: 'high' },
  { id: 'MEDIUM', icon: AlertTriangle, cls: 'medium' },
  { id: 'LOW', icon: CheckCircle, cls: 'low' },
]

export default function DashboardPage() {
  // Event stats — refresh mỗi 10s
  const {
    data: stats,
    isLoading: loadingStats,
    mutate: mutateStats,
  } = useSWR('/api/events/stats', eventsApi.stats, { refreshInterval: 10000 })

  // Camera list — refresh mỗi 30s
  const {
    data: cameras = [],
    isLoading: loadingCams,
    mutate: mutateCams,
  } = useSWR('/api/cameras', camerasApi.list, { refreshInterval: 30000 })

  // Event feed — refresh mỗi 10s
  const {
    data: events = [],
    isLoading: loadingEvents,
    mutate: mutateEvents,
  } = useSWR('/api/events?limit=20', () => eventsApi.list({ limit: 20 }), { refreshInterval: 10000 })

  const handleRefresh = () => {
    mutateStats()
    mutateCams()
    mutateEvents()
  }

  const bySev = stats?.by_severity ?? {}
  const maxSev = Math.max(1, ...Object.values(bySev))
  const loading = loadingStats || loadingEvents

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* Stat cards */}
      <div className="stats-grid">
        <div className="stat-card" style={{ '--card-accent': 'var(--brand)' } as any}>
          <Activity className="stat-card__icon" />
          <div className="stat-card__label">Tổng Alert Hôm Nay</div>
          <div className="stat-card__value">{loadingStats ? '—' : stats?.total ?? 0}</div>
          <div className="stat-card__sub">{stats?.date ?? '...'}</div>
        </div>

        {SEV_LABELS.slice(0, 2).map(({ id, icon: Icon, cls }) => (
          <div key={id} className="stat-card" style={{ '--card-accent': `var(--sev-${cls})` } as any}>
            <Icon className="stat-card__icon" />
            <div className="stat-card__label">{id}</div>
            <div className="stat-card__value" style={{ color: `var(--sev-${cls})` }}>
              {loadingStats ? '—' : bySev[id] ?? 0}
            </div>
          </div>
        ))}

        <div className="stat-card" style={{ '--card-accent': 'var(--success)' } as any}>
          <CheckCircle className="stat-card__icon" />
          <div className="stat-card__label">LOW / MEDIUM</div>
          <div className="stat-card__value" style={{ color: 'var(--success)' }}>
            {loadingStats ? '—' : (bySev['LOW'] ?? 0) + (bySev['MEDIUM'] ?? 0)}
          </div>
        </div>
      </div>

      {/* Live Camera Preview */}
      <div className="card">
        <div className="card__title">
          <Video size={16} /> Video trực tiếp
        </div>
        {loadingCams ? (
          <div className="empty-state">
            <Clock size={36} className="empty-state__icon" /> Đang tải danh sách camera...
          </div>
        ) : (
          <CameraGrid cameras={cameras} />
        )}
      </div>

      {/* Charts + Top cameras */}
      <div className="grid-2">
        {/* Bar chart */}
        <div className="card">
          <div className="card__title">
            <BarChart3 size={16} /> Phân bố cảnh báo
          </div>
          <div className="chart-area" style={{ minHeight: 150 }}>
            {SEV_LABELS.map(({ id, cls }) => (
              <div
                key={id}
                className="chart-bar"
                title={`${id}: ${bySev[id] ?? 0}`}
                style={{
                  height: `${Math.max(4, ((bySev[id] ?? 0) / maxSev) * 120)}px`,
                  background: `linear-gradient(to top, var(--sev-${cls}), var(--sev-${cls})80)`,
                }}
              />
            ))}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-around', marginTop: 8 }}>
            {SEV_LABELS.map(({ id, icon: Icon, cls }) => (
              <span key={id} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, color: 'var(--text-muted)' }}>
                <Icon size={12} style={{ color: `var(--sev-${cls})` }} /> {id.slice(0, 3)}
              </span>
            ))}
          </div>
        </div>

        {/* Top cameras */}
        <div className="card">
          <div className="card__title">
            <CameraIcon size={16} /> Top Camera
          </div>
          {loadingStats ? (
            <div className="empty-state"><Clock size={36} className="empty-state__icon" /> Đang tải...</div>
          ) : (stats?.top_cameras?.length ?? 0) === 0 ? (
            <div className="empty-state"><ShieldCheck size={36} className="empty-state__icon" /> Chưa có dữ liệu</div>
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
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Activity size={16} /> Cảnh báo gần nhất
          </div>
          <button className="btn btn--ghost btn--sm" onClick={handleRefresh}>
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Tải lại
          </button>
        </div>

        {loadingEvents && events.length === 0 ? (
          <div className="empty-state"><Clock size={36} className="empty-state__icon" /> Đang tải...</div>
        ) : events.length === 0 ? (
          <div className="empty-state">
            <ShieldCheck size={36} className="empty-state__icon" style={{ color: 'var(--success)' }} />
            Hệ thống an toàn / Không có sự kiện
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
                    <CameraIcon size={10} style={{ display: 'inline', verticalAlign: 'middle', marginRight: 2 }} />
                    {evt.camera_id ?? '?'} &nbsp;|&nbsp;
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
