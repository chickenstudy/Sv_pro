import { useState, useEffect, useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Search, Car, Camera as CameraIcon, Clock,
  SlidersHorizontal, RefreshCw, Plus, Gauge, ChevronLeft, ChevronRight, X,
  CalendarDays, ShieldAlert, ShieldCheck, ImageOff,
} from 'lucide-react'
import {
  lprApi, vehiclesApi, detectImageUrl,
  type LprEvent, type LprEventDetail, type LprStats,
  LPR_CATEGORY_LABEL,
} from '../api'
import { useToast } from '../components/Toast'

// ── Constants ─────────────────────────────────────────────────────────────────
const PAGE_SIZE = 15

// Phân loại biển — hiển thị + filter dropdown
const CATEGORY_OPTIONS = [
  { value: '',                label: 'Tất cả phân loại' },
  { value: 'XE_MAY_DAN_SU',   label: LPR_CATEGORY_LABEL.XE_MAY_DAN_SU },
  { value: 'O_TO_DAN_SU',     label: LPR_CATEGORY_LABEL.O_TO_DAN_SU },
  { value: 'BIEN_CA_NHAN',    label: LPR_CATEGORY_LABEL.BIEN_CA_NHAN },
  { value: 'XE_MAY_DIEN',     label: LPR_CATEGORY_LABEL.XE_MAY_DIEN },
  { value: 'XE_QUAN_DOI',     label: LPR_CATEGORY_LABEL.XE_QUAN_DOI },
  { value: 'KHONG_XAC_DINH',  label: LPR_CATEGORY_LABEL.KHONG_XAC_DINH },
  { value: 'NOT_DETECTED',    label: LPR_CATEGORY_LABEL.NOT_DETECTED },
]

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtTime(ts?: string | null) {
  if (!ts) return '--:--:--'
  try { return new Date(ts).toLocaleTimeString('vi-VN', { hour12: false }) }
  catch { return ts }
}
function fmtFull(ts?: string | null) {
  if (!ts) return '---'
  try { return new Date(ts).toLocaleString('vi-VN', { hour12: false }) }
  catch { return ts }
}
function todayISO() { return new Date().toISOString().split('T')[0] }
function categoryLabel(cat?: string | null): string {
  if (!cat) return '---'
  return (LPR_CATEGORY_LABEL as Record<string, string>)[cat] ?? cat
}

// ── Pagination ────────────────────────────────────────────────────────────────
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
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4, padding: '10px 0' }}>
      <button onClick={() => onChange(page - 1)} disabled={page <= 1} style={{ ...pgBtn, opacity: page <= 1 ? 0.4 : 1 }}>
        <ChevronLeft size={14} />
      </button>
      {pages.map((p, i) =>
        p === '…'
          ? <span key={`e${i}`} style={{ padding: '0 4px', color: 'var(--text-muted)', fontSize: 12 }}>…</span>
          : <button key={p} onClick={() => onChange(p as number)} style={{ ...pgBtn, ...(p === page ? pgActive : {}) }}>{p}</button>
      )}
      <button onClick={() => onChange(page + 1)} disabled={page >= totalPages} style={{ ...pgBtn, opacity: page >= totalPages ? 0.4 : 1 }}>
        <ChevronRight size={14} />
      </button>
      <span style={{ marginLeft: 8, fontSize: 11, color: 'var(--text-muted)' }}>
        {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} / {total}
      </span>
    </div>
  )
}
const pgBtn: React.CSSProperties = {
  minWidth: 28, height: 28, padding: '0 6px',
  background: 'var(--bg-elevated)', border: '1px solid var(--border)',
  borderRadius: 'var(--r-sm)', color: 'var(--text-secondary)',
  fontSize: 12, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
}
const pgActive: React.CSSProperties = { background: 'var(--brand)', borderColor: 'var(--brand)', color: '#fff', fontWeight: 700 }

// ── Plate thumbnail (crop biển số) ─────────────────────────────────────────────
function PlateThumb({ ev }: { ev: LprEvent }) {
  const url = detectImageUrl(ev.plate_image_path) || detectImageUrl(ev.image_path)
  const plate = ev.plate_number ?? '???'
  const [failed, setFailed] = useState(false)

  if (url && !failed) {
    return (
      <img src={url} alt={plate} onError={() => setFailed(true)}
        style={{ width: 110, height: 38, objectFit: 'cover', borderRadius: 4,
          border: '1px solid var(--border)', display: 'block', background: '#000' }} />
    )
  }
  return (
    <div style={{
      width: 110, height: 38, background: '#e8e8d8', border: '2px solid #444',
      borderRadius: 5, display: 'flex', alignItems: 'center', justifyContent: 'center',
      position: 'relative', overflow: 'hidden', flexShrink: 0,
    }}>
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 7, background: '#1a4fa0', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ fontSize: 5, color: '#fff', fontWeight: 700, letterSpacing: .5 }}>VIỆT NAM</span>
      </div>
      <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11, fontWeight: 900, color: '#111', letterSpacing: .5, marginTop: 4 }}>{plate}</span>
    </div>
  )
}

// ── Detail modal: ảnh xe lớn + crop biển + metadata ────────────────────────────
function LprModal({ ev, detail, loading, onClose, onAddToLib }: {
  ev: LprEvent
  detail: LprEventDetail | null
  loading: boolean
  onClose: () => void
  onAddToLib: (plate: string, category?: string) => Promise<void>
}) {
  const toast = useToast()
  const plate    = detail?.plate_number ?? ev.plate_number ?? '???'
  const category = detail?.plate_category ?? ev.plate_category ?? '---'
  const conf     = detail?.ocr_confidence ?? ev.ocr_confidence ?? null
  const detConf  = detail?.plate_det_confidence ?? ev.plate_det_confidence ?? null
  const vehicleUrl = detectImageUrl(detail?.image_path ?? ev.image_path)
  const plateUrl   = detectImageUrl(detail?.plate_image_path ?? ev.plate_image_path)
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState(false)

  useEffect(() => {
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', fn)
    return () => document.removeEventListener('keydown', fn)
  }, [onClose])

  const handleAdd = async () => {
    setAdding(true)
    try {
      await onAddToLib(plate, category === '---' ? undefined : category)
      setAdded(true)
      toast.success(`Đã thêm ${plate} vào thư viện phương tiện`)
    } catch (e: any) {
      const msg = e?.message ?? 'Lỗi không xác định'
      toast.error(msg.includes('409') || msg.includes('exists') ? `${plate} đã có trong thư viện` : `Lỗi: ${msg}`)
    } finally {
      setAdding(false)
    }
  }

  return (
    <div onClick={e => { if (e.target === e.currentTarget) onClose() }}
      style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,.75)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
      <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-xl)', width: '100%', maxWidth: 720, maxHeight: '90vh', overflow: 'auto', boxShadow: '0 24px 64px rgba(0,0,0,.6)' }}>
        {/* Header */}
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, position: 'sticky', top: 0, background: 'var(--bg-elevated)', zIndex: 1 }}>
          <Car size={16} color="var(--brand)" />
          <span style={{ fontWeight: 700, fontSize: 13, flex: 1 }}>Chi tiết nhận diện biển số</span>
          {loading && <RefreshCw size={12} color="var(--text-muted)" style={{ animation: 'spin 1s linear infinite' }} />}
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: 4 }}>
            <X size={16} />
          </button>
        </div>

        <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Images: vehicle (16:9) + plate crop (1:1) */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 160px', gap: 12 }}>
            <div>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 4, fontWeight: 600, letterSpacing: .5 }}>KHUNG HÌNH XE</div>
              {vehicleUrl
                ? <img src={vehicleUrl} alt="vehicle"
                    onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                    style={{ width: '100%', aspectRatio: '16/9', objectFit: 'contain', background: '#000', borderRadius: 'var(--r-md)', border: '1px solid var(--border)' }} />
                : <div style={{ aspectRatio: '16/9', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 6, color: 'var(--text-muted)' }}>
                    <ImageOff size={28} strokeWidth={1} />
                    <span style={{ fontSize: 11 }}>Không có ảnh xe</span>
                  </div>
              }
            </div>
            <div>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 4, fontWeight: 600, letterSpacing: .5 }}>CROP BIỂN SỐ</div>
              {plateUrl
                ? <img src={plateUrl} alt="plate"
                    onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                    style={{ width: '100%', aspectRatio: '1/1', objectFit: 'contain', background: '#000', borderRadius: 'var(--r-md)', border: '2px solid var(--border)' }} />
                : <div style={{ aspectRatio: '1/1', background: 'var(--bg-surface)', border: '2px solid var(--border)', borderRadius: 'var(--r-md)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
                    <ImageOff size={20} strokeWidth={1} />
                  </div>
              }
            </div>
          </div>

          {/* Plate hero */}
          <div style={{ padding: '12px 16px', background: 'var(--bg-hover)', borderRadius: 'var(--r-md)', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 6 }}>BIỂN SỐ XE</div>
            <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 26, fontWeight: 800, letterSpacing: 4, color: 'var(--text-primary)' }}>
              {plate}
            </span>
          </div>

          {/* Metadata */}
          <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', overflow: 'hidden' }}>
            {[
              { label: 'Camera',     value: ev.camera_id ?? '---', icon: <CameraIcon size={11} /> },
              { label: 'Thời điểm',  value: fmtFull(ev.timestamp), icon: <Clock size={11} /> },
              { label: 'Phân loại',  value: categoryLabel(category), icon: <Car size={11} /> },
              { label: 'Loại xe',    value: ev.label ?? '---' },
            ].map((row, i, arr) => (
              <div key={row.label} style={{ display: 'flex', padding: '9px 14px', borderBottom: i < arr.length - 1 ? '1px solid var(--border)' : 'none', alignItems: 'center', gap: 6 }}>
                <div style={{ width: 110, fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
                  {row.icon} {row.label}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-primary)' }}>{row.value}</div>
              </div>
            ))}
            {conf != null && <ConfRow label="OCR Confidence" value={conf} />}
            {detConf != null && <ConfRow label="Detection Confidence" value={detConf} />}
          </div>

          {/* Add to lib */}
          {category !== 'NOT_DETECTED' && plate !== '???' && (
            <button
              onClick={handleAdd} disabled={adding || added}
              style={{ padding: '9px 14px', background: added ? 'var(--success-glow)' : 'var(--brand-glow)', border: `1px solid ${added ? 'var(--success)' : 'var(--brand)'}`, borderRadius: 'var(--r-md)', color: added ? 'var(--success)' : 'var(--brand)', fontSize: 12, fontWeight: 600, cursor: adding || added ? 'default' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}
            >
              {adding ? <RefreshCw size={14} style={{ animation: 'spin 1s linear infinite' }} /> : <Plus size={14} />}
              {adding ? 'Đang thêm...' : added ? 'Đã thêm vào thư viện' : 'Thêm vào thư viện phương tiện'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function ConfRow({ label, value }: { label: string; value: number }) {
  const pct = Math.round(value * 100)
  const color = pct >= 80 ? 'var(--success)' : pct >= 55 ? 'var(--warning)' : 'var(--danger)'
  return (
    <div style={{ padding: '9px 14px', borderTop: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <div style={{ width: 110, fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 5 }}>
          <Gauge size={11} /> {label}
        </div>
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ flex: 1, height: 6, background: 'var(--border)', borderRadius: 3, overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 3 }} />
          </div>
          <span style={{ fontSize: 12, fontFamily: 'JetBrains Mono, monospace', color, minWidth: 38, textAlign: 'right' }}>{pct}%</span>
        </div>
      </div>
    </div>
  )
}

// ── Stats strip ───────────────────────────────────────────────────────────────
function StatsStrip({ stats, total }: { stats: LprStats | null; total: number }) {
  const totalLpr = stats?.total ?? total
  const success  = stats
    ? Object.entries(stats.by_category).filter(([k]) => k !== 'NOT_DETECTED' && k !== 'KHONG_XAC_DINH').reduce((a, [, v]) => a + v, 0)
    : total
  const notDet   = stats?.by_category.NOT_DETECTED ?? 0
  const unknown  = stats?.by_category.KHONG_XAC_DINH ?? 0
  const successPct = totalLpr > 0 ? Math.round((success / totalLpr) * 100) : 0

  const items = [
    { label: 'Tổng lượt detect',  value: totalLpr,  icon: <Car size={14} />,           color: 'var(--brand)' },
    { label: 'Đọc thành công',     value: success,   icon: <ShieldCheck size={14} />,   color: 'var(--success)' },
    { label: 'Không đọc được',     value: notDet,    icon: <ImageOff size={14} />,      color: 'var(--text-muted)' },
    { label: 'Không xác định',     value: unknown,   icon: <ShieldAlert size={14} />,   color: 'var(--warning)' },
    { label: 'Tỉ lệ đọc',          value: `${successPct}%`, icon: <Gauge size={14} />, color: successPct >= 50 ? 'var(--success)' : 'var(--warning)' },
  ]
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 8 }}>
      {items.map(s => (
        <div key={s.label} style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ color: s.color, opacity: 0.85 }}>{s.icon}</div>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: s.color, lineHeight: 1.2, fontFamily: 'JetBrains Mono, monospace' }}>{s.value}</div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>{s.label}</div>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function LprPage() {
  const today = todayISO()
  const [searchParams] = useSearchParams()
  const [events, setEvents] = useState<LprEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [search, setSearch] = useState(searchParams.get('plate') ?? '')
  const [cameraFilter, setCameraFilter] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [date, setDate] = useState(today)
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<LprEvent | null>(null)
  const [detail, setDetail] = useState<LprEventDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [serverTotal, setServerTotal] = useState(0)
  const [stats, setStats] = useState<LprStats | null>(null)

  const isToday = date === today

  const load = useCallback(async (silent = false) => {
    silent ? setRefreshing(true) : setLoading(true)
    try {
      const result = await lprApi.list({
        date,
        category: categoryFilter || undefined,
        camera:   cameraFilter   || undefined,
        search:   search.trim()  || undefined,
        limit: 500,
      })
      setEvents(result.data)
      setServerTotal(result.total)
    } catch (e) { console.error(e) }
    finally { setLoading(false); setRefreshing(false) }
  }, [date, categoryFilter, cameraFilter, search])

  useEffect(() => { load() }, [load])

  // Stats
  useEffect(() => {
    lprApi.stats(date).then(setStats).catch(() => setStats(null))
  }, [date])

  // Auto-refresh 20s khi xem hôm nay
  useEffect(() => {
    if (!isToday) return
    const t = setInterval(() => { load(true); lprApi.stats(date).then(setStats).catch(() => {}) }, 20_000)
    return () => clearInterval(t)
  }, [isToday, load, date])

  // Reset page khi đổi filter
  useEffect(() => { setPage(1) }, [search, cameraFilter, categoryFilter, date])

  // Load detail khi mở modal
  useEffect(() => {
    if (!selected) { setDetail(null); return }
    setDetailLoading(true)
    lprApi.getDetail(selected.id)
      .then(setDetail).catch(() => setDetail(null))
      .finally(() => setDetailLoading(false))
  }, [selected])

  const handleAddToLib = async (plate: string, category?: string) => {
    await vehiclesApi.create({ plate_number: plate, plate_category: category ?? null })
  }

  const cameras = useMemo(
    () => Array.from(new Set(events.map(e => e.camera_id).filter(Boolean))),
    [events],
  )
  const pageData = events.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Toolbar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Car size={16} color="var(--brand)" />
        <span style={{ fontWeight: 700, fontSize: 14 }}>Nhận diện Biển số xe</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4, padding: '2px 6px' }}>
          {serverTotal} bản ghi
        </span>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {/* Date */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <CalendarDays size={11} color="var(--text-muted)" />
            <input type="date" value={date} max={today}
              onChange={e => setDate(e.target.value)}
              style={inputStyle} />
          </div>
          {/* Category */}
          <select value={categoryFilter} onChange={e => setCategoryFilter(e.target.value)}
            style={{ ...inputStyle, paddingLeft: 8, cursor: 'pointer' }}>
            {CATEGORY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          {/* Search */}
          <div style={{ position: 'relative' }}>
            <Search size={12} style={{ position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)', pointerEvents: 'none' }} />
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Tìm biển số..."
              style={{ ...inputStyle, paddingLeft: 26, width: 150 }} />
          </div>
          {/* Camera */}
          <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
            <SlidersHorizontal size={11} style={{ position: 'absolute', left: 7, color: 'var(--text-muted)', pointerEvents: 'none' }} />
            <select value={cameraFilter} onChange={e => setCameraFilter(e.target.value)}
              style={{ ...inputStyle, paddingLeft: 24, cursor: 'pointer', color: cameraFilter ? 'var(--text-primary)' : 'var(--text-muted)' }}>
              <option value="">Tất cả camera</option>
              {cameras.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          {/* Refresh */}
          <button onClick={() => load(true)} disabled={refreshing}
            style={{ height: 30, width: 30, background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', cursor: 'pointer', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <RefreshCw size={12} style={{ animation: refreshing ? 'spin 1s linear infinite' : 'none' }} />
          </button>
        </div>
      </div>

      {/* Stats */}
      {!loading && <StatsStrip stats={stats} total={serverTotal} />}

      {/* Table */}
      <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
            <RefreshCw size={24} style={{ animation: 'spin 1s linear infinite' }} />
            <span style={{ fontSize: 12 }}>Đang tải dữ liệu...</span>
          </div>
        ) : events.length === 0 ? (
          <div style={{ padding: 48, textAlign: 'center', color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
            <Car size={36} strokeWidth={1} />
            <span style={{ fontSize: 13 }}>
              {search || cameraFilter || categoryFilter ? 'Không tìm thấy biển số phù hợp' : 'Chưa có dữ liệu nhận diện biển số trong ngày này'}
            </span>
          </div>
        ) : (
          <>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-surface)' }}>
                  {['#', 'Ảnh biển số', 'Biển số xe', 'Camera', 'Thời điểm', 'Phân loại', 'Loại xe', 'OCR'].map(h => (
                    <th key={h} style={{ padding: '9px 12px', textAlign: 'left', fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: .5, whiteSpace: 'nowrap' }}>
                      {h.toUpperCase()}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {pageData.map((ev, idx) => {
                  const rowNum = (page - 1) * PAGE_SIZE + idx + 1
                  const isNotDet = ev.category === 'NOT_DETECTED' || !ev.plate_number
                  const conf = ev.ocr_confidence ?? null
                  const confColor = conf == null ? 'var(--text-muted)' :
                    conf >= 0.8 ? 'var(--success)' : conf >= 0.55 ? 'var(--warning)' : 'var(--danger)'
                  return (
                    <tr key={ev.id} onClick={() => setSelected(ev)}
                      style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer', background: isNotDet ? 'rgba(120,120,120,0.05)' : 'transparent' }}
                      onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-hover)')}
                      onMouseLeave={e => (e.currentTarget.style.background = isNotDet ? 'rgba(120,120,120,0.05)' : 'transparent')}
                    >
                      <td style={{ padding: '8px 12px', color: 'var(--text-muted)', fontSize: 11 }}>{rowNum}</td>
                      <td style={{ padding: '8px 12px' }}><PlateThumb ev={ev} /></td>
                      <td style={{ padding: '8px 12px' }}>
                        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 14, letterSpacing: 1, color: isNotDet ? 'var(--text-muted)' : 'var(--text-primary)' }}>
                          {ev.plate_number ?? '— —'}
                        </span>
                      </td>
                      <td style={{ padding: '8px 12px' }}>
                        <span style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 4 }}>
                          <CameraIcon size={10} /> {ev.camera_id}
                        </span>
                      </td>
                      <td style={{ padding: '8px 12px', fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap', fontFamily: 'JetBrains Mono, monospace' }}>
                        {fmtTime(ev.timestamp)}
                      </td>
                      <td style={{ padding: '8px 12px', fontSize: 11, color: 'var(--text-secondary)' }}>
                        {categoryLabel(ev.plate_category ?? ev.category)}
                      </td>
                      <td style={{ padding: '8px 12px', fontSize: 11, color: 'var(--text-secondary)', textTransform: 'capitalize' }}>
                        {ev.label ?? '---'}
                      </td>
                      <td style={{ padding: '8px 12px', fontSize: 11, fontFamily: 'JetBrains Mono, monospace', color: confColor }}>
                        {conf != null ? `${Math.round(conf * 100)}%` : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>

            <div style={{ borderTop: '1px solid var(--border)', padding: '4px 12px' }}>
              <Pagination page={page} total={events.length} pageSize={PAGE_SIZE} onChange={p => { setPage(p); window.scrollTo({ top: 0 }) }} />
            </div>
          </>
        )}
      </div>

      {/* Detail modal */}
      {selected && (
        <LprModal ev={selected} detail={detail} loading={detailLoading}
          onClose={() => setSelected(null)} onAddToLib={handleAddToLib} />
      )}
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  height: 30, background: 'var(--bg-surface)', border: '1px solid var(--border)',
  borderRadius: 'var(--r-sm)', color: 'var(--text-primary)', fontSize: 12,
  padding: '0 8px', outline: 'none',
}
