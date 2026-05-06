import { useState, useEffect, useCallback } from 'react'
import { BarChart2, RefreshCw, CalendarDays, Car, ScanFace, Activity, TrendingUp } from 'lucide-react'
import { eventsApi, AccessEvent } from '../api'

// ── Helpers ───────────────────────────────────────────────────────────────────
function todayISO() { return new Date().toISOString().split('T')[0] }
function daysAgo(n: number) {
  const d = new Date(); d.setDate(d.getDate() - n)
  return d.toISOString().split('T')[0]
}
function toStartOfDay(d: string) { return `${d}T00:00:00+07:00` }
function toEndOfDay(d: string)   { return `${d}T23:59:59+07:00` }
function fmtShortDate(iso: string) {
  const d = new Date(iso + 'T00:00:00')
  return d.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit' })
}

// ── SVG Bar Chart ─────────────────────────────────────────────────────────────
function BarChart({ data, color = 'var(--brand)' }: {
  data: Array<{ label: string; value: number }>; color?: string
}) {
  if (!data.length) return <div style={{ height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: 12 }}>Không có dữ liệu</div>
  const max = Math.max(...data.map(d => d.value), 1)
  const W = 500, H = 100, barW = Math.max(4, Math.floor((W - data.length * 2) / data.length))
  return (
    <svg viewBox={`0 0 ${W} ${H + 22}`} style={{ width: '100%', height: 'auto', overflow: 'visible' }}>
      {data.map((d, i) => {
        const x = i * (barW + 2)
        const barH = Math.max(2, (d.value / max) * H)
        return (
          <g key={i}>
            <rect x={x} y={H - barH} width={barW} height={barH} fill={color} rx={2} opacity={0.85} />
            {barW >= 20 && (
              <text x={x + barW / 2} y={H + 14} textAnchor="middle" fontSize={9} fill="var(--text-muted)">{d.label}</text>
            )}
            {d.value > 0 && (
              <text x={x + barW / 2} y={H - barH - 3} textAnchor="middle" fontSize={9} fill="var(--text-muted)">{d.value}</text>
            )}
          </g>
        )
      })}
    </svg>
  )
}

// ── SVG Pie Chart ─────────────────────────────────────────────────────────────
function PieChart({ slices }: { slices: Array<{ label: string; value: number; color: string }> }) {
  const total = slices.reduce((s, x) => s + x.value, 0)
  if (total === 0) return <div style={{ height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: 12 }}>Không có dữ liệu</div>
  let angle = -Math.PI / 2
  const R = 50, cx = 60, cy = 60
  const paths = slices.filter(s => s.value > 0).map(s => {
    const a = (s.value / total) * 2 * Math.PI
    const x1 = cx + R * Math.cos(angle), y1 = cy + R * Math.sin(angle)
    const x2 = cx + R * Math.cos(angle + a), y2 = cy + R * Math.sin(angle + a)
    const large = a > Math.PI ? 1 : 0
    const path = `M${cx},${cy} L${x1},${y1} A${R},${R},0,${large},1,${x2},${y2} Z`
    angle += a
    return { path, color: s.color, label: s.label, value: s.value, pct: Math.round((s.value / total) * 100) }
  })
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
      <svg viewBox="0 0 120 120" style={{ width: 100, height: 100, flexShrink: 0 }}>
        {paths.map((p, i) => <path key={i} d={p.path} fill={p.color} stroke="var(--bg-elevated)" strokeWidth={1} />)}
      </svg>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {paths.map(p => (
          <div key={p.label} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
            <div style={{ width: 10, height: 10, borderRadius: 2, background: p.color, flexShrink: 0 }} />
            <span style={{ color: 'var(--text-muted)' }}>{p.label}</span>
            <span style={{ fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-primary)', marginLeft: 4 }}>{p.value} ({p.pct}%)</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Horizontal bar for top cameras ────────────────────────────────────────────
function HBar({ items, color = 'var(--brand)' }: { items: Array<{ label: string; value: number }>; color?: string }) {
  const max = Math.max(...items.map(i => i.value), 1)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      {items.map(item => (
        <div key={item.label} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 90, fontSize: 11, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flexShrink: 0 }}>{item.label}</div>
          <div style={{ flex: 1, height: 14, background: 'var(--bg-surface)', borderRadius: 3, overflow: 'hidden' }}>
            <div style={{ width: `${(item.value / max) * 100}%`, height: '100%', background: color, borderRadius: 3, transition: 'width .4s ease' }} />
          </div>
          <div style={{ width: 30, textAlign: 'right', fontSize: 11, fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-secondary)', flexShrink: 0 }}>{item.value}</div>
        </div>
      ))}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
type TabId = 'overview' | 'lpr' | 'fr'
const TABS: { id: TabId; label: string; icon: typeof Activity }[] = [
  { id: 'overview', label: 'Tổng quan', icon: Activity },
  { id: 'lpr',      label: 'Biển số xe', icon: Car },
  { id: 'fr',       label: 'Khuôn mặt', icon: ScanFace },
]

export default function ReportsPage() {
  const [tab, setTab] = useState<TabId>('overview')
  const [fromDate, setFromDate] = useState(daysAgo(6))
  const [toDate, setToDate] = useState(todayISO())
  const [events, setEvents] = useState<AccessEvent[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await eventsApi.list({
        from: toStartOfDay(fromDate),
        to:   toEndOfDay(toDate),
        limit: 2000,
      })
      setEvents(data)
    } catch (e) { console.error(e) }
    finally { setLoading(false) }
  }, [fromDate, toDate])

  useEffect(() => { load() }, [load])

  // ── Aggregations ──────────────────────────────────────────────────────────

  // Events per day (for bar chart spanning date range)
  const days: string[] = []
  {
    const start = new Date(fromDate + 'T00:00:00')
    const end   = new Date(toDate + 'T00:00:00')
    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1))
      days.push(d.toISOString().split('T')[0])
  }
  const eventsByDay = days.map(day => ({
    label: fmtShortDate(day),
    value: events.filter(e => e.event_timestamp.startsWith(day)).length,
  }))

  // Events by hour (today-like aggregation)
  const eventsByHour = Array.from({ length: 24 }, (_, h) => ({
    label: h % 3 === 0 ? String(h).padStart(2, '0') : '',
    value: events.filter(e => new Date(e.event_timestamp).getHours() === h).length,
  }))

  // Severity breakdown
  const sevCounts = { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 }
  events.forEach(e => { if (e.severity in sevCounts) (sevCounts as any)[e.severity]++ })
  const severitySlices = [
    { label: 'CRITICAL', value: sevCounts.CRITICAL, color: 'var(--sev-critical)' },
    { label: 'HIGH',     value: sevCounts.HIGH,     color: 'var(--sev-high)' },
    { label: 'MEDIUM',   value: sevCounts.MEDIUM,   color: 'var(--sev-medium)' },
    { label: 'LOW',      value: sevCounts.LOW,       color: 'var(--sev-low)' },
  ]

  // Top cameras
  const camCount: Record<string, number> = {}
  events.forEach(e => { if (e.camera_id) camCount[e.camera_id] = (camCount[e.camera_id] ?? 0) + 1 })
  const topCams = Object.entries(camCount).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([label, value]) => ({ label, value }))

  // LPR: by day table
  const lprEvents = events.filter(e => e.event_type === 'lpr_recognition')
  const lprByDay = days.map(day => {
    const dayEvs = lprEvents.filter(e => e.event_timestamp.startsWith(day))
    const cams: Record<string, number> = {}
    dayEvs.forEach(e => { if (e.camera_id) cams[e.camera_id] = (cams[e.camera_id] ?? 0) + 1 })
    return { day, total: dayEvs.length, topCam: Object.entries(cams).sort((a,b)=>b[1]-a[1])[0]?.[0] ?? '—' }
  }).filter(r => r.total > 0)

  // FR stats
  const frEvents = events.filter(e => e.event_type === 'face_recognition' || e.event_type === 'stranger_detected' || e.event_type === 'blacklist_person')
  const known    = frEvents.filter(e => e.event_type === 'face_recognition').length
  const stranger = frEvents.filter(e => e.event_type === 'stranger_detected').length
  const blacklistFr = frEvents.filter(e => e.event_type === 'blacklist_person').length
  const frSlices = [
    { label: 'Đã nhận diện', value: known,       color: 'var(--success)' },
    { label: 'Người lạ',     value: stranger,    color: '#f97316' },
    { label: 'Blacklist',    value: blacklistFr, color: 'var(--sev-critical)' },
  ]

  // Top identified persons
  const personCount: Record<string, number> = {}
  frEvents.filter(e => e.event_type === 'face_recognition' && e.reason).forEach(e => {
    const name = e.reason!; personCount[name] = (personCount[name] ?? 0) + 1
  })
  const topPersons = Object.entries(personCount).sort((a,b)=>b[1]-a[1]).slice(0,8).map(([label,value]) => ({ label, value }))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <BarChart2 size={16} color="var(--brand)" />
        <span style={{ fontWeight: 700, fontSize: 14 }}>Báo cáo & Thống kê</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4, padding: '2px 6px' }}>
          {events.length} sự kiện
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
          <CalendarDays size={11} color="var(--text-muted)" />
          <input type="date" value={fromDate} max={toDate}
            onChange={e => setFromDate(e.target.value)}
            style={dateInput} />
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>→</span>
          <input type="date" value={toDate} min={fromDate}
            onChange={e => setToDate(e.target.value)}
            style={dateInput} />
          <button onClick={load} disabled={loading}
            style={{ height: 30, width: 30, background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', cursor: 'pointer', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <RefreshCw size={12} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }} />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 2, background: 'var(--bg-elevated)', borderRadius: 'var(--r-md)', padding: 3, width: 'fit-content' }}>
        {TABS.map(t => {
          const Icon = t.icon
          return (
            <button key={t.id} onClick={() => setTab(t.id)}
              style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '5px 14px', borderRadius: 'var(--r-sm)', border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600, transition: 'all var(--t-quick)', background: tab === t.id ? 'var(--brand)' : 'transparent', color: tab === t.id ? '#fff' : 'var(--text-muted)' }}>
              <Icon size={13} /> {t.label}
            </button>
          )
        })}
      </div>

      {loading ? (
        <div style={{ padding: 60, textAlign: 'center', color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
          <RefreshCw size={24} style={{ animation: 'spin 1s linear infinite' }} />
          <span style={{ fontSize: 12 }}>Đang tải dữ liệu...</span>
        </div>
      ) : (
        <>
          {/* ── Overview Tab ── */}
          {tab === 'overview' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {/* Summary stat strip */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
                {[
                  { label: 'Tổng sự kiện', value: events.length, color: 'var(--brand)' },
                  { label: 'Cảnh báo cao', value: sevCounts.HIGH + sevCounts.CRITICAL, color: 'var(--sev-high)' },
                  { label: 'Đọc biển số', value: lprEvents.length, color: 'var(--success)' },
                  { label: 'Nhận diện mặt', value: frEvents.length, color: '#a78bfa' },
                ].map(s => (
                  <div key={s.label} style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '10px 14px' }}>
                    <div style={{ fontSize: 22, fontWeight: 700, color: s.color, fontFamily: 'JetBrains Mono, monospace' }}>{s.value}</div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{s.label}</div>
                  </div>
                ))}
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                {/* Events per day bar chart */}
                <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '14px 16px' }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
                    <TrendingUp size={13} /> Sự kiện theo ngày
                  </div>
                  <BarChart data={eventsByDay} color="var(--brand)" />
                </div>

                {/* Events by hour */}
                <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '14px 16px' }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Activity size={13} /> Phân bố theo giờ trong ngày
                  </div>
                  <BarChart data={eventsByHour} color="var(--accent)" />
                </div>

                {/* Severity pie */}
                <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '14px 16px' }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10 }}>
                    Phân bố mức độ (Severity)
                  </div>
                  <PieChart slices={severitySlices} />
                </div>

                {/* Top cameras */}
                <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '14px 16px' }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10 }}>
                    Top camera nhiều sự kiện nhất
                  </div>
                  {topCams.length > 0 ? <HBar items={topCams} color="var(--success)" /> : <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Không có dữ liệu</div>}
                </div>
              </div>
            </div>
          )}

          {/* ── LPR Tab ── */}
          {tab === 'lpr' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {/* LPR bar chart */}
              <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '14px 16px' }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10 }}>Số lần đọc biển số theo ngày</div>
                <BarChart data={days.map(day => ({ label: fmtShortDate(day), value: lprEvents.filter(e => e.event_timestamp.startsWith(day)).length }))} color="var(--success)" />
              </div>

              {/* LPR daily table */}
              <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', overflow: 'hidden' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-surface)' }}>
                      {['Ngày', 'Tổng lượt đọc', 'Camera chính'].map(h => (
                        <th key={h} style={{ padding: '9px 14px', textAlign: 'left', fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: .5 }}>{h.toUpperCase()}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {lprByDay.length === 0 ? (
                      <tr><td colSpan={3} style={{ padding: '24px 14px', textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>Không có dữ liệu LPR</td></tr>
                    ) : lprByDay.map(row => (
                      <tr key={row.day} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '8px 14px', fontFamily: 'JetBrains Mono, monospace', fontSize: 12 }}>{fmtShortDate(row.day)}</td>
                        <td style={{ padding: '8px 14px' }}>
                          <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, color: 'var(--success)', fontSize: 14 }}>{row.total}</span>
                        </td>
                        <td style={{ padding: '8px 14px', fontSize: 11, color: 'var(--text-muted)' }}>{row.topCam}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── FR Tab ── */}
          {tab === 'fr' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                {/* FR pie */}
                <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '14px 16px' }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10 }}>Tỉ lệ nhận diện khuôn mặt</div>
                  <PieChart slices={frSlices} />
                </div>

                {/* FR summary stats */}
                <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 2 }}>Tóm tắt nhận diện mặt</div>
                  {[
                    { label: 'Tổng lượt nhận diện', value: frEvents.length, color: 'var(--text-primary)' },
                    { label: 'Đã đăng ký', value: known, color: 'var(--success)' },
                    { label: 'Người lạ', value: stranger, color: '#f97316' },
                    { label: 'Blacklist', value: blacklistFr, color: 'var(--sev-critical)' },
                    { label: 'Tỉ lệ nhận diện', value: frEvents.length > 0 ? `${Math.round((known / frEvents.length) * 100)}%` : '—', color: 'var(--brand)' },
                  ].map(s => (
                    <div key={s.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
                      <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{s.label}</span>
                      <span style={{ fontSize: 14, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, color: s.color }}>{s.value}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Top persons */}
              {topPersons.length > 0 && (
                <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '14px 16px' }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10 }}>Top nhân viên được nhận diện nhiều nhất</div>
                  <HBar items={topPersons} color="#a78bfa" />
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

const dateInput: React.CSSProperties = {
  height: 30, background: 'var(--bg-surface)', border: '1px solid var(--border)',
  borderRadius: 'var(--r-sm)', color: 'var(--text-primary)', fontSize: 12, padding: '0 8px', outline: 'none',
}
