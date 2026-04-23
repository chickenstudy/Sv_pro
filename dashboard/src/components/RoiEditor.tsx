/**
 * ROI Editor — vẽ polygon vùng quan tâm trên 1 frame snapshot của camera.
 *
 * Toạ độ lưu CHUẨN HOÁ [0,1] để không lệ thuộc vào resolution camera. Khi
 * AI Core áp dụng, multiply lại với (width, height) frame thực tế.
 *
 * Tương tác:
 *   - Click ảnh   → thêm 1 điểm polygon
 *   - Click điểm  → xoá điểm đó
 *   - Drag điểm   → kéo di chuyển
 *   - Nút Reset   → xoá toàn bộ polygon
 *   - Nút Refresh → snapshot lại (nếu cảnh thay đổi)
 */

import { useEffect, useRef, useState } from 'react'
import { RefreshCw, Trash2, Save, X, Eraser } from 'lucide-react'
import { camerasApi, type Camera, type RoiPoint } from '../api'

interface RoiEditorProps {
  camera: Camera
  onSaved: (cam: Camera) => void
  onClose: () => void
}

export default function RoiEditor({ camera, onSaved, onClose }: RoiEditorProps) {
  const [snapshotUrl, setSnapshotUrl] = useState<string>(camerasApi.snapshotUrl(camera.id))
  const [imgSize, setImgSize]   = useState<{ w: number; h: number }>({ w: 800, h: 450 })
  const [points, setPoints]     = useState<RoiPoint[]>(camera.roi_polygon ?? [])
  const [draggingIdx, setDragIdx] = useState<number | null>(null)
  const [busy, setBusy]         = useState(false)
  const [err, setErr]           = useState('')
  const containerRef = useRef<HTMLDivElement>(null)

  const refreshSnapshot = () => setSnapshotUrl(camerasApi.snapshotUrl(camera.id))

  const onImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget
    setImgSize({ w: img.naturalWidth, h: img.naturalHeight })
  }

  const containerToNorm = (clientX: number, clientY: number): RoiPoint | null => {
    const el = containerRef.current
    if (!el) return null
    const rect = el.getBoundingClientRect()
    const x = (clientX - rect.left) / rect.width
    const y = (clientY - rect.top) / rect.height
    if (x < 0 || x > 1 || y < 0 || y > 1) return null
    return { x: Math.round(x * 1000) / 1000, y: Math.round(y * 1000) / 1000 }
  }

  // Add point on background click
  const handleBgClick = (e: React.MouseEvent) => {
    if (draggingIdx !== null) { setDragIdx(null); return }
    const pt = containerToNorm(e.clientX, e.clientY)
    if (!pt) return
    setPoints(prev => [...prev, pt])
  }

  // Delete point on shift-click, drag otherwise
  const handlePointMouseDown = (idx: number, e: React.MouseEvent) => {
    e.stopPropagation()
    if (e.shiftKey) {
      setPoints(prev => prev.filter((_, i) => i !== idx))
      return
    }
    setDragIdx(idx)
  }

  useEffect(() => {
    if (draggingIdx === null) return
    const onMove = (e: MouseEvent) => {
      const pt = containerToNorm(e.clientX, e.clientY)
      if (!pt) return
      setPoints(prev => prev.map((p, i) => i === draggingIdx ? pt : p))
    }
    const onUp = () => setDragIdx(null)
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [draggingIdx])

  const save = async () => {
    setBusy(true); setErr('')
    try {
      // Validation: polygon đủ ý nghĩa cần ≥3 điểm. Nếu rỗng → clear ROI (null).
      if (points.length > 0 && points.length < 3) {
        throw new Error('Polygon cần ≥ 3 điểm (hoặc xoá hết để bỏ ROI)')
      }
      const payload = points.length === 0 ? null : points
      const updated = await camerasApi.update(camera.id, { roi_polygon: payload })
      onSaved(updated)
    } catch (e: any) {
      setErr(e?.message || 'Lỗi lưu ROI')
    } finally {
      setBusy(false)
    }
  }

  // Convert points → SVG polygon string (% coords)
  const polyAttr = points.map(p => `${(p.x * 100).toFixed(2)}%,${(p.y * 100).toFixed(2)}%`).join(' ')

  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1000, padding: 16,
      }}
      onClick={onClose}
    >
      <div
        className="card"
        style={{ width: 880, maxWidth: '95vw', maxHeight: '95vh', overflow: 'auto' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="card__title" style={{ justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Eraser size={16} /> ROI editor — {camera.name}
          </div>
          <button className="btn btn--ghost btn--sm" onClick={onClose}>
            <X size={14} />
          </button>
        </div>

        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 0 }}>
          <strong>Cách dùng:</strong> click ảnh để thêm điểm · drag điểm để di chuyển ·
          <kbd style={{ padding: '0 4px', background: 'var(--bg)', borderRadius: 3 }}>Shift</kbd>+click điểm để xoá ·
          ≥ 3 điểm = polygon hợp lệ · để rỗng = bỏ ROI (full frame).
        </p>

        {/* Snapshot + overlay */}
        <div
          ref={containerRef}
          onClick={handleBgClick}
          style={{
            position: 'relative',
            width: '100%',
            background: '#000',
            borderRadius: 'var(--r-md)',
            overflow: 'hidden',
            cursor: draggingIdx !== null ? 'grabbing' : 'crosshair',
            aspectRatio: imgSize.w && imgSize.h ? `${imgSize.w} / ${imgSize.h}` : '16 / 9',
          }}
        >
          <img
            src={snapshotUrl}
            alt={`snapshot ${camera.name}`}
            onLoad={onImageLoad}
            style={{
              display: 'block',
              width: '100%', height: '100%', objectFit: 'contain',
              userSelect: 'none', pointerEvents: 'none',
            }}
            draggable={false}
          />

          {/* SVG overlay */}
          <svg
            style={{
              position: 'absolute', inset: 0, width: '100%', height: '100%',
              pointerEvents: 'none',
            }}
          >
            {points.length >= 2 && (
              <polygon
                points={polyAttr}
                fill="rgba(0, 192, 255, 0.18)"
                stroke="cyan"
                strokeWidth={2}
                strokeLinejoin="round"
              />
            )}
            {points.length === 1 && (
              <circle
                cx={`${points[0].x * 100}%`}
                cy={`${points[0].y * 100}%`}
                r={4}
                fill="cyan"
              />
            )}
            {points.map((p, i) => (
              <g key={i}>
                <circle
                  cx={`${p.x * 100}%`}
                  cy={`${p.y * 100}%`}
                  r={9}
                  fill="cyan"
                  fillOpacity={0.3}
                  stroke="cyan"
                  strokeWidth={2}
                  style={{ cursor: 'grab', pointerEvents: 'auto' }}
                  onMouseDown={e => handlePointMouseDown(i, e as any)}
                />
                <text
                  x={`${p.x * 100}%`}
                  y={`${p.y * 100}%`}
                  dy="0.35em"
                  textAnchor="middle"
                  fill="#000"
                  fontSize={10}
                  fontWeight={700}
                  style={{ pointerEvents: 'none', userSelect: 'none' }}
                >
                  {i + 1}
                </text>
              </g>
            ))}
          </svg>
        </div>

        {/* Status + actions */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          marginTop: 10, flexWrap: 'wrap',
        }}>
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            {points.length === 0
              ? '⚠️ Chưa có ROI — sẽ dùng full frame'
              : `${points.length} điểm · ${points.length >= 3 ? '✓ polygon hợp lệ' : '⚠️ cần thêm điểm'}`}
          </span>
          <button
            className="btn btn--ghost btn--sm"
            onClick={refreshSnapshot}
            title="Snapshot lại từ camera"
          >
            <RefreshCw size={12} /> Refresh ảnh
          </button>
          <button
            className="btn btn--ghost btn--sm"
            onClick={() => setPoints([])}
            disabled={points.length === 0}
          >
            <Trash2 size={12} /> Xoá hết điểm
          </button>
          <button
            className="btn btn--primary btn--sm"
            onClick={save}
            disabled={busy || (points.length > 0 && points.length < 3)}
            style={{ marginLeft: 'auto' }}
          >
            {busy ? <RefreshCw size={12} className="animate-spin" /> : <Save size={12} />}
            &nbsp;Lưu ROI
          </button>
        </div>

        {err && (
          <div style={{
            marginTop: 8,
            color: 'var(--danger)', fontSize: 12,
            padding: '8px 12px',
            background: 'var(--danger)15',
            borderRadius: 'var(--r-md)',
          }}>
            ❌ {err}
          </div>
        )}
      </div>
    </div>
  )
}
