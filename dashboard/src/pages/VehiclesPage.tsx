import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import useSWR from 'swr'
import { vehiclesApi, type Vehicle } from '../api'
import { useToast } from '../components/Toast'
import {
  Car, Ban, Plus, X, Search, Save, CheckCircle, CarFront,
  ChevronLeft, ChevronRight, History, RefreshCw, Trash2,
} from 'lucide-react'

const VEH_PAGE_SIZE = 20

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtDate(ts: string) {
  return new Date(ts).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' })
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
  const btn: React.CSSProperties = {
    minWidth: 28, height: 28, padding: '0 6px',
    background: 'var(--bg-elevated)', border: '1px solid var(--border)',
    borderRadius: 'var(--r-sm)', color: 'var(--text-secondary)',
    fontSize: 12, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
  }
  const active: React.CSSProperties = { background: 'var(--brand)', borderColor: 'var(--brand)', color: '#fff', fontWeight: 700 }
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4, padding: '10px 0' }}>
      <button onClick={() => onChange(page - 1)} disabled={page <= 1} style={{ ...btn, opacity: page <= 1 ? 0.4 : 1 }}><ChevronLeft size={14} /></button>
      {pages.map((p, i) =>
        p === '…'
          ? <span key={`e${i}`} style={{ padding: '0 4px', color: 'var(--text-muted)', fontSize: 12 }}>…</span>
          : <button key={p} onClick={() => onChange(p as number)} style={{ ...btn, ...(p === page ? active : {}) }}>{p}</button>
      )}
      <button onClick={() => onChange(page + 1)} disabled={page >= totalPages} style={{ ...btn, opacity: page >= totalPages ? 0.4 : 1 }}><ChevronRight size={14} /></button>
      <span style={{ marginLeft: 8, fontSize: 11, color: 'var(--text-muted)' }}>
        {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} / {total}
      </span>
    </div>
  )
}

// ── Bulk blacklist confirm dialog ─────────────────────────────────────────────
function BulkDialog({ plates, action, onConfirm, onCancel }: {
  plates: string[]; action: 'blacklist' | 'remove'
  onConfirm: (reason?: string) => void; onCancel: () => void
}) {
  const [reason, setReason] = useState('')
  const isBlacklist = action === 'blacklist'

  useEffect(() => {
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', fn); return () => document.removeEventListener('keydown', fn)
  }, [onCancel])

  return (
    <div onClick={e => { if (e.target === e.currentTarget) onCancel() }}
      style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
      <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-xl)', width: '100%', maxWidth: 460, boxShadow: '0 24px 64px rgba(0,0,0,.5)', padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {isBlacklist ? <Ban size={16} color="var(--danger)" /> : <CheckCircle size={16} color="var(--success)" />}
          <span style={{ fontWeight: 700, fontSize: 13 }}>
            {isBlacklist ? `Blacklist ${plates.length} phương tiện` : `Gỡ blacklist ${plates.length} phương tiện`}
          </span>
        </div>
        <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--r-md)', padding: '10px 12px', display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {plates.map(p => (
            <span key={p} style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11, fontWeight: 700, padding: '2px 7px', background: isBlacklist ? 'var(--danger-glow)' : 'var(--success-glow)', border: `1px solid ${isBlacklist ? 'var(--danger)' : 'var(--success)'}`, borderRadius: 4, color: isBlacklist ? 'var(--danger)' : 'var(--success)' }}>{p}</span>
          ))}
        </div>
        {isBlacklist && (
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Lý do blacklist</div>
            <input value={reason} onChange={e => setReason(e.target.value)} placeholder="Nhập lý do..."
              style={{ width: '100%', height: 34, padding: '0 10px', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', color: 'var(--text-primary)', fontSize: 12, outline: 'none', boxSizing: 'border-box' }} />
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onCancel}
            style={{ height: 32, padding: '0 14px', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', cursor: 'pointer', color: 'var(--text-secondary)', fontSize: 12 }}>
            Hủy
          </button>
          <button onClick={() => onConfirm(isBlacklist ? reason || undefined : undefined)} disabled={isBlacklist && !reason}
            style={{ height: 32, padding: '0 14px', background: isBlacklist ? 'var(--danger)' : 'var(--success)', border: 'none', borderRadius: 'var(--r-sm)', cursor: isBlacklist && !reason ? 'default' : 'pointer', color: '#fff', fontSize: 12, fontWeight: 600, opacity: isBlacklist && !reason ? 0.6 : 1 }}>
            {isBlacklist ? 'Xác nhận Blacklist' : 'Xác nhận Gỡ'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function VehiclesPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [blOnly, setBlOnly] = useState(false)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const { data: vehicles = [], error, isLoading, mutate } = useSWR(
    ['/api/vehicles', blOnly],
    () => vehiclesApi.list(blOnly)
  )
  useEffect(() => { setPage(1) }, [blOnly, search])

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ plate_number: '', plate_category: '', is_blacklisted: false, blacklist_reason: '' })
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState('')

  // Bulk selection
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkDialog, setBulkDialog] = useState<'blacklist' | 'remove' | null>(null)
  const [bulkWorking, setBulkWorking] = useState(false)

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault(); setSaving(true); setFormError('')
    try {
      await vehiclesApi.create(form)
      setShowForm(false)
      setForm({ plate_number: '', plate_category: '', is_blacklisted: false, blacklist_reason: '' })
      mutate()
      toast.success(`Đã thêm xe ${form.plate_number}`)
    } catch (err: any) {
      const msg = err?.message ?? 'Lỗi không xác định'
      setFormError(msg)
      toast.error(msg)
    }
    finally { setSaving(false) }
  }

  const handleToggle = async (v: Vehicle) => {
    if (v.is_blacklisted) {
      if (!confirm(`Xóa "${v.plate_number}" khỏi blacklist?`)) return
      await vehiclesApi.toggleBlacklist(v.plate_number, false)
      toast.success(`Đã gỡ blacklist ${v.plate_number}`)
    } else {
      const reason = prompt(`Lý do blacklist "${v.plate_number}":`)
      if (!reason) return
      await vehiclesApi.toggleBlacklist(v.plate_number, true, reason)
      toast.warn(`Đã thêm ${v.plate_number} vào danh sách đen`)
    }
    mutate()
  }

  const toggleSelect = (plate: string) => {
    setSelected(prev => {
      const s = new Set(prev)
      s.has(plate) ? s.delete(plate) : s.add(plate)
      return s
    })
  }
  const toggleSelectAll = () => {
    if (selected.size === pageData.length) setSelected(new Set())
    else setSelected(new Set(pageData.map(v => v.plate_number)))
  }

  const handleBulkConfirm = async (reason?: string) => {
    setBulkWorking(true)
    const action = bulkDialog!
    const count = selected.size
    try {
      await Promise.all(
        Array.from(selected).map(plate =>
          vehiclesApi.toggleBlacklist(plate, action === 'blacklist', reason)
        )
      )
      setSelected(new Set())
      setBulkDialog(null)
      mutate()
      action === 'blacklist'
        ? toast.warn(`Đã blacklist ${count} phương tiện`)
        : toast.success(`Đã gỡ blacklist ${count} phương tiện`)
    } catch (err) {
      toast.error('Có lỗi xảy ra khi thực hiện bulk action')
      console.error(err)
    }
    finally { setBulkWorking(false) }
  }

  // Client-side search filter
  const filteredVehicles = search.trim()
    ? vehicles.filter(v => v.plate_number.toUpperCase().includes(search.toUpperCase().trim()))
    : vehicles

  const pageData = filteredVehicles.slice((page - 1) * VEH_PAGE_SIZE, page * VEH_PAGE_SIZE)
  const allOnPageSelected = pageData.length > 0 && pageData.every(v => selected.has(v.plate_number))
  const someSelected = selected.size > 0

  const selectedPlates = Array.from(selected)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Car size={16} color="var(--brand)" />
        <span style={{ fontWeight: 700, fontSize: 14 }}>Quản lý Phương tiện</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4, padding: '2px 6px' }}>
          {filteredVehicles.length} xe
        </span>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
          {/* Search */}
          <div style={{ position: 'relative' }}>
            <Search size={12} style={{ position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)', pointerEvents: 'none' }} />
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Tìm biển số..."
              style={{ paddingLeft: 26, paddingRight: 8, height: 30, background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', color: 'var(--text-primary)', fontSize: 12, outline: 'none', width: 150 }} />
          </div>
          {/* Blacklist only toggle */}
          <button onClick={() => setBlOnly(v => !v)}
            style={{ height: 30, padding: '0 10px', background: blOnly ? 'var(--danger-glow)' : 'var(--bg-surface)', border: `1px solid ${blOnly ? 'var(--danger)' : 'var(--border)'}`, borderRadius: 'var(--r-sm)', cursor: 'pointer', color: blOnly ? 'var(--danger)' : 'var(--text-muted)', fontSize: 12, display: 'flex', alignItems: 'center', gap: 5 }}>
            <Ban size={12} /> {blOnly ? 'Đang lọc BL' : 'Lọc Blacklist'}
          </button>
          {/* Add vehicle */}
          <button onClick={() => setShowForm(v => !v)}
            style={{ height: 30, padding: '0 10px', background: showForm ? 'var(--bg-hover)' : 'var(--brand)', border: `1px solid ${showForm ? 'var(--border)' : 'var(--brand)'}`, borderRadius: 'var(--r-sm)', cursor: 'pointer', color: showForm ? 'var(--text-secondary)' : '#fff', fontSize: 12, display: 'flex', alignItems: 'center', gap: 5, fontWeight: 600 }}>
            {showForm ? <><X size={12} /> Đóng</> : <><Plus size={12} /> Thêm xe</>}
          </button>
        </div>
      </div>

      {/* Add form */}
      {showForm && (
        <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '14px 16px' }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
            <Plus size={14} /> Thêm xe mới
          </div>
          <form onSubmit={handleCreate} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 10, alignItems: 'flex-end' }}>
            <div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Biển số *</div>
              <input required value={form.plate_number}
                onChange={e => setForm(f => ({ ...f, plate_number: e.target.value.toUpperCase() }))}
                placeholder="51A-12345"
                style={{ width: '100%', height: 32, padding: '0 10px', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', color: 'var(--text-primary)', fontSize: 12, outline: 'none', fontFamily: 'JetBrains Mono, monospace', boxSizing: 'border-box' }} />
            </div>
            <div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Loại phương tiện</div>
              <select value={form.plate_category} onChange={e => setForm(f => ({ ...f, plate_category: e.target.value }))}
                style={{ width: '100%', height: 32, padding: '0 10px', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', color: form.plate_category ? 'var(--text-primary)' : 'var(--text-muted)', fontSize: 12, outline: 'none', boxSizing: 'border-box' }}>
                <option value="">Chọn loại</option>
                <option value="car">Ô tô</option>
                <option value="motorcycle">Xe máy</option>
                <option value="truck">Xe tải</option>
                <option value="bus">Xe buýt</option>
              </select>
            </div>
            <button type="submit" disabled={saving}
              style={{ height: 32, padding: '0 14px', background: 'var(--brand)', border: 'none', borderRadius: 'var(--r-sm)', cursor: saving ? 'default' : 'pointer', color: '#fff', fontSize: 12, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5 }}>
              {saving ? <RefreshCw size={12} style={{ animation: 'spin 1s linear infinite' }} /> : <Save size={12} />}
              {saving ? 'Đang lưu...' : 'Lưu'}
            </button>
          </form>
          {formError && <div style={{ color: 'var(--danger)', fontSize: 12, marginTop: 8 }}>{formError}</div>}
        </div>
      )}

      {/* Bulk action bar */}
      {someSelected && (
        <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--brand)', borderRadius: 'var(--r-md)', padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 12, color: 'var(--brand)', fontWeight: 600 }}>{selected.size} xe đã chọn</span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
            <button onClick={() => setBulkDialog('blacklist')} disabled={bulkWorking}
              style={{ height: 28, padding: '0 10px', background: 'var(--danger-glow)', border: '1px solid var(--danger)', borderRadius: 'var(--r-sm)', cursor: 'pointer', color: 'var(--danger)', fontSize: 12, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 4 }}>
              <Ban size={12} /> Blacklist ({selected.size})
            </button>
            <button onClick={() => setBulkDialog('remove')} disabled={bulkWorking}
              style={{ height: 28, padding: '0 10px', background: 'var(--success-glow)', border: '1px solid var(--success)', borderRadius: 'var(--r-sm)', cursor: 'pointer', color: 'var(--success)', fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
              <Trash2 size={12} /> Gỡ Blacklist ({selected.size})
            </button>
            <button onClick={() => setSelected(new Set())}
              style={{ height: 28, padding: '0 8px', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', cursor: 'pointer', color: 'var(--text-muted)', fontSize: 12 }}>
              <X size={12} />
            </button>
          </div>
        </div>
      )}

      {error && (
        <div style={{ color: 'var(--danger)', fontSize: 12, padding: '8px 12px', background: 'rgba(239,68,68,.08)', borderRadius: 'var(--r-md)', border: '1px solid var(--danger)' }}>
          {error?.message || 'Có lỗi xảy ra'}
        </div>
      )}

      {/* Table */}
      <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', overflow: 'hidden' }}>
        {isLoading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
            <RefreshCw size={24} style={{ animation: 'spin 1s linear infinite' }} />
            <span style={{ fontSize: 12 }}>Đang tải...</span>
          </div>
        ) : filteredVehicles.length === 0 ? (
          <div style={{ padding: 48, textAlign: 'center', color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
            <CarFront size={36} strokeWidth={1} />
            <span style={{ fontSize: 13 }}>{blOnly ? 'Không có xe nào trong blacklist' : search ? 'Không tìm thấy biển số phù hợp' : 'Chưa có xe nào được đăng ký'}</span>
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-surface)' }}>
                <th style={{ padding: '9px 12px', width: 36 }}>
                  <input type="checkbox" checked={allOnPageSelected} onChange={toggleSelectAll}
                    style={{ cursor: 'pointer', width: 14, height: 14 }} />
                </th>
                {['Biển số', 'Loại', 'Trạng thái', 'Lý do', 'Ngày đăng ký', 'Hành động'].map(h => (
                  <th key={h} style={{ padding: '9px 12px', textAlign: 'left', fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: .5, whiteSpace: 'nowrap' }}>
                    {h.toUpperCase()}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pageData.map(v => (
                <tr key={v.id}
                  style={{ borderBottom: '1px solid var(--border)', background: selected.has(v.plate_number) ? 'var(--brand-glow)' : v.is_blacklisted ? 'rgba(239,68,68,0.04)' : 'transparent', transition: 'background var(--t-quick)' }}
                  onMouseEnter={e => { if (!selected.has(v.plate_number)) e.currentTarget.style.background = 'var(--bg-hover)' }}
                  onMouseLeave={e => { e.currentTarget.style.background = selected.has(v.plate_number) ? 'var(--brand-glow)' : v.is_blacklisted ? 'rgba(239,68,68,0.04)' : 'transparent' }}
                >
                  <td style={{ padding: '8px 12px' }}>
                    <input type="checkbox" checked={selected.has(v.plate_number)} onChange={() => toggleSelect(v.plate_number)}
                      style={{ cursor: 'pointer', width: 14, height: 14 }} />
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 14, letterSpacing: 1, color: v.is_blacklisted ? 'var(--danger)' : 'var(--text-primary)' }}>
                      {v.plate_number}
                    </span>
                  </td>
                  <td style={{ padding: '8px 12px', fontSize: 11, color: 'var(--text-muted)' }}>
                    {v.plate_category ?? '—'}
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    {v.is_blacklisted
                      ? <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--danger)', background: 'var(--danger-glow)', border: '1px solid var(--danger)', borderRadius: 4, padding: '2px 7px', display: 'inline-flex', alignItems: 'center', gap: 4 }}><Ban size={10} /> Blacklist</span>
                      : <span style={{ fontSize: 10, color: 'var(--success)', background: 'var(--success-glow)', border: '1px solid var(--success)', borderRadius: 4, padding: '2px 7px', display: 'inline-flex', alignItems: 'center', gap: 4 }}><CheckCircle size={10} /> Bình thường</span>
                    }
                  </td>
                  <td style={{ padding: '8px 12px', maxWidth: 180 }}>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v.blacklist_reason ?? '—'}</div>
                  </td>
                  <td style={{ padding: '8px 12px', fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                    {fmtDate(v.registered_at)}
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    <div style={{ display: 'flex', gap: 5 }}>
                      {/* LPR history link */}
                      <button onClick={() => navigate(`/lpr?plate=${encodeURIComponent(v.plate_number)}`)}
                        style={{ height: 26, padding: '0 8px', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', cursor: 'pointer', color: 'var(--text-muted)', fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}
                        title="Xem lịch sử nhận diện biển số">
                        <History size={11} /> LPR
                      </button>
                      {/* Toggle blacklist */}
                      <button onClick={() => handleToggle(v)}
                        style={{ height: 26, padding: '0 8px', background: v.is_blacklisted ? 'var(--success-glow)' : 'var(--danger-glow)', border: `1px solid ${v.is_blacklisted ? 'var(--success)' : 'var(--danger)'}`, borderRadius: 'var(--r-sm)', cursor: 'pointer', color: v.is_blacklisted ? 'var(--success)' : 'var(--danger)', fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}>
                        {v.is_blacklisted ? <><CheckCircle size={11} /> Gỡ BL</> : <><Ban size={11} /> Blacklist</>}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {filteredVehicles.length > VEH_PAGE_SIZE && (
          <div style={{ borderTop: '1px solid var(--border)', padding: '4px 12px' }}>
            <Pagination page={page} total={filteredVehicles.length} pageSize={VEH_PAGE_SIZE} onChange={setPage} />
          </div>
        )}
      </div>

      {/* Bulk confirm dialog */}
      {bulkDialog && (
        <BulkDialog
          plates={selectedPlates}
          action={bulkDialog}
          onConfirm={handleBulkConfirm}
          onCancel={() => setBulkDialog(null)}
        />
      )}
    </div>
  )
}
