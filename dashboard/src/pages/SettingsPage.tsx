/**
 * Settings Page — quản lý retention (TTL ảnh + DB rows).
 *
 * Hiển thị 4 control:
 *   - retention.detect_days       (ảnh /Detect)
 *   - retention.audit_days        (audit JSON)
 *   - retention.events_days       (access_events + recognition_logs)
 *   - retention.guest_faces_days  (stranger embeddings)
 *
 * Có nút "Chạy dọn dẹp ngay" + lịch sử các lần chạy.
 */

import { useState } from 'react'
import useSWR from 'swr'
import {
  Settings as SettingsIcon, Save, RefreshCw, Trash2, Clock, AlertTriangle,
  CheckCircle, Database, Image as ImageIcon, Activity, UserX, PlayCircle,
} from 'lucide-react'
import { settingsApi, type AppSetting } from '../api'

interface RetentionGroup {
  key:    string
  label:  string
  desc:   string
  icon:   React.ElementType
}

const GROUPS: RetentionGroup[] = [
  {
    key:   'retention.detect_days',
    label: 'Ảnh detection (faces / plates)',
    desc:  'Thư mục /Detect/{cam}/ và /Detect/faces/{cam}/ — file JPG do AI Core lưu',
    icon:  ImageIcon,
  },
  {
    key:   'retention.audit_days',
    label: 'Audit log (BlacklistEngine sidecar)',
    desc:  'Thư mục /Detect/audit/ — JSON event của BlacklistEngine',
    icon:  Database,
  },
  {
    key:   'retention.events_days',
    label: 'Events (access_events + recognition_logs)',
    desc:  'Hàng trong DB — feed dashboard và trang Events',
    icon:  Activity,
  },
  {
    key:   'retention.guest_faces_days',
    label: 'Guest faces (stranger embeddings)',
    desc:  'Bảng guest_faces — Re-ID memory cho người lạ',
    icon:  UserX,
  },
]

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function fmtTime(ts: string | null): string {
  if (!ts) return '—'
  try { return new Date(ts).toLocaleString('vi-VN') }
  catch { return ts }
}

// ── Single retention row ──────────────────────────────────────────────────────

function RetentionCard({
  group, current, onSaved,
}: {
  group:   RetentionGroup
  current: AppSetting | undefined
  onSaved: () => void
}) {
  const initial = typeof current?.value === 'number' ? current.value : 30
  const [days, setDays] = useState<number>(initial)
  const [busy, setBusy] = useState(false)
  const [err, setErr]   = useState('')
  const [ok, setOk]     = useState(false)
  const Icon = group.icon

  const dirty = days !== initial

  const save = async () => {
    setBusy(true); setErr(''); setOk(false)
    try {
      if (days < 1 || days > 3650) throw new Error('Giá trị phải trong [1, 3650] ngày')
      // Cảnh báo khi giảm mạnh (xoá nhiều dữ liệu lần cleanup tới)
      if (days < initial && initial - days >= 30) {
        if (!confirm(`Giảm từ ${initial} → ${days} ngày sẽ xoá nhiều dữ liệu ở lần cleanup tới. Tiếp tục?`)) {
          setBusy(false); return
        }
      }
      await settingsApi.update(group.key, days)
      setOk(true)
      onSaved()
      setTimeout(() => setOk(false), 2500)
    } catch (e: any) {
      setErr(e?.message || 'Lỗi lưu')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <Icon size={20} style={{ color: 'var(--brand)', flexShrink: 0, marginTop: 2 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>{group.label}</div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
            {group.desc}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
            <input
              type="number"
              className="input"
              style={{ width: 100, fontSize: 13 }}
              min={1}
              max={3650}
              value={days}
              onChange={e => setDays(Math.max(1, Math.min(3650, parseInt(e.target.value) || 1)))}
            />
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>ngày</span>
            <button
              className={`btn btn--sm ${dirty ? 'btn--primary' : 'btn--ghost'}`}
              disabled={!dirty || busy}
              onClick={save}
            >
              {busy ? <RefreshCw size={12} className="animate-spin" /> : <Save size={12} />}
              &nbsp;Lưu
            </button>
            {ok && (
              <span style={{ fontSize: 11, color: 'var(--success)', display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                <CheckCircle size={11} /> Đã lưu
              </span>
            )}
            {err && (
              <span style={{ fontSize: 11, color: 'var(--danger)' }}>{err}</span>
            )}
          </div>
          {current?.updated_at && (
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 6 }}>
              Cập nhật lần cuối: {fmtTime(current.updated_at)}
              {current.updated_by ? ` bởi ${current.updated_by}` : ''}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const { data: list = [], mutate } = useSWR('/api/settings', settingsApi.list)
  const { data: runs = [], mutate: mutateRuns } = useSWR(
    '/api/settings/cleanup/runs',
    settingsApi.listRuns,
    { refreshInterval: 60_000 },
  )

  const byKey = new Map(list.map(s => [s.key, s]))
  const lastRun = runs[0]

  const [running, setRunning] = useState(false)
  const [runResult, setRunResult] = useState<{ ok: boolean; text: string } | null>(null)

  const triggerCleanup = async () => {
    if (!confirm('Chạy dọn dẹp ngay theo cấu hình hiện tại? File quá hạn sẽ bị xoá vĩnh viễn.')) return
    setRunning(true); setRunResult(null)
    try {
      const r = await settingsApi.runCleanup()
      const rowSummary = Object.entries(r.rows_deleted || {})
        .map(([k, v]) => `${k}=${v}`).join(', ')
      setRunResult({
        ok: !r.error,
        text: r.error
          ? `Lỗi: ${r.error}`
          : `✓ Xoá ${r.files_deleted} file (${fmtBytes(r.bytes_deleted)}) · DB: ${rowSummary} · ${r.took_seconds}s`,
      })
      mutateRuns()
    } catch (e: any) {
      setRunResult({ ok: false, text: e?.message || 'Lỗi' })
    } finally {
      setRunning(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <SettingsIcon size={26} color="var(--brand)" />
        <div>
          <h2 style={{ margin: 0, fontSize: 18 }}>Cài đặt hệ thống</h2>
          <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
            Retention TTL cho ảnh + log · Cron tự động chạy 02:15 mỗi ngày
          </span>
        </div>
      </div>

      {/* Retention controls */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))',
        gap: 12,
      }}>
        {GROUPS.map(g => (
          <RetentionCard
            key={g.key}
            group={g}
            current={byKey.get(g.key)}
            onSaved={mutate}
          />
        ))}
      </div>

      {/* Manual cleanup card */}
      <div className="card">
        <div className="card__title">
          <PlayCircle size={16} /> Chạy dọn dẹp thủ công
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <button
            className="btn btn--danger"
            onClick={triggerCleanup}
            disabled={running}
          >
            {running ? <RefreshCw size={14} className="animate-spin" /> : <Trash2 size={14} />}
            &nbsp;{running ? 'Đang chạy...' : 'Chạy dọn dẹp ngay'}
          </button>
          {lastRun && (
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Lần chạy gần nhất: <span className="font-mono">{fmtTime(lastRun.started_at)}</span>
              {' · '}
              <span style={{ color: lastRun.error ? 'var(--danger)' : 'var(--success)' }}>
                {lastRun.error ? '✗ Lỗi' : '✓ OK'}
              </span>
              {!lastRun.error && (
                <> · {lastRun.deleted_files} file ({fmtBytes(lastRun.deleted_bytes)})</>
              )}
            </div>
          )}
        </div>
        {runResult && (
          <div style={{
            marginTop: 8,
            color: runResult.ok ? 'var(--success)' : 'var(--danger)',
            fontSize: 12,
            padding: '8px 12px',
            background: runResult.ok ? 'var(--success)15' : 'var(--danger)15',
            borderRadius: 'var(--r-md)',
          }}>
            {runResult.text}
          </div>
        )}
      </div>

      {/* Run history */}
      <div className="card">
        <div className="card__title">
          <Clock size={16} /> Lịch sử cleanup (20 lần gần nhất)
        </div>
        {runs.length === 0 ? (
          <div className="empty-state">
            <AlertTriangle size={28} className="empty-state__icon" style={{ opacity: 0.4 }} />
            Chưa có lần cleanup nào
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-muted)', fontSize: 11 }}>
                  <th style={{ padding: 6, textAlign: 'left' }}>Thời gian</th>
                  <th style={{ padding: 6, textAlign: 'left' }}>Trigger</th>
                  <th style={{ padding: 6, textAlign: 'right' }}>File xoá</th>
                  <th style={{ padding: 6, textAlign: 'right' }}>Dung lượng</th>
                  <th style={{ padding: 6, textAlign: 'left' }}>DB rows</th>
                  <th style={{ padding: 6, textAlign: 'left' }}>Trạng thái</th>
                </tr>
              </thead>
              <tbody>
                {runs.map(r => (
                  <tr key={r.id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td className="font-mono" style={{ padding: 6 }}>{fmtTime(r.started_at)}</td>
                    <td style={{ padding: 6 }}>
                      <span className={`badge badge--${r.triggered_by.startsWith('manual') ? 'brand' : 'low'}`}>
                        {r.triggered_by}
                      </span>
                    </td>
                    <td style={{ padding: 6, textAlign: 'right' }} className="font-mono">{r.deleted_files}</td>
                    <td style={{ padding: 6, textAlign: 'right' }} className="font-mono">{fmtBytes(r.deleted_bytes)}</td>
                    <td style={{ padding: 6, fontSize: 11 }}>
                      {r.deleted_rows
                        ? Object.entries(r.deleted_rows).map(([k,v]) => `${k}=${v}`).join(', ')
                        : '—'}
                    </td>
                    <td style={{ padding: 6 }}>
                      {r.error
                        ? <span className="badge badge--critical" title={r.error}>✗ Lỗi</span>
                        : <span className="badge badge--success">✓ OK</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
