/**
 * DetectionOverlay — canvas overlay vẽ bbox detection realtime lên video stream.
 *
 * Kết nối WebSocket `/api/stream/detections/{sourceId}` để nhận metadata từ
 * backend (publish bởi BlacklistEngine → Redis pub/sub). Mỗi frame có dạng:
 *   {ts, source_id, frame_w, frame_h, detections: [{label, bbox, person_name, ...}]}
 *
 * Canvas absolute positioning trên video element, resize auto theo video size.
 * Bbox trên frame gốc (vd 2592×1944) → scale về canvas size (vd 400×300).
 * Chỉ vẽ detection có ts < 500ms cũ (bbox cũ bị loại khỏi canvas).
 */

import { useEffect, useRef } from 'react'
import { getToken } from '../api'

interface Detection {
  label:           string
  bbox:            [number, number, number, number]   // x1, y1, x2, y2 trên frame gốc
  track_id?:       number | null
  person_id?:      string
  person_name?:    string
  person_role?:    string
  fr_confidence?:  number
  plate_number?:   string
  plate_category?: string
}

interface DetectionFrame {
  ts:          number
  source_id:   string
  frame_w:     number
  frame_h:     number
  detections:  Detection[]
}

interface Props {
  sourceId:      string
  videoRef:      React.RefObject<HTMLVideoElement>
  enabled:       boolean
  /** max age (seconds) — bbox cũ hơn sẽ bị clear khỏi canvas */
  maxAgeSec?:    number
}

const ROLE_COLOR: Record<string, string> = {
  staff:     '#22c55e',    // xanh lá
  admin:     '#3b82f6',    // xanh dương
  blacklist: '#ef4444',    // đỏ
  guest:     '#f59e0b',    // vàng
  unknown:   '#64748b',    // xám (stranger)
}

export function DetectionOverlay({ sourceId, videoRef, enabled, maxAgeSec = 0.5 }: Props) {
  const canvasRef  = useRef<HTMLCanvasElement>(null)
  const latestRef  = useRef<DetectionFrame | null>(null)
  const rafRef     = useRef<number | null>(null)

  // ── WebSocket subscribe ────────────────────────────────────────────────────
  useEffect(() => {
    if (!enabled || !sourceId) return
    const token = getToken()
    if (!token) return

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url   = `${proto}//${location.host}/api/stream/detections/${encodeURIComponent(sourceId)}?t=${encodeURIComponent(token)}`

    let ws: WebSocket | null = null
    let closed = false
    let retryTimer: ReturnType<typeof setTimeout> | null = null

    const connect = () => {
      if (closed) return
      try {
        ws = new WebSocket(url)
      } catch (e) {
        console.warn(`[DetectionOverlay ${sourceId}] ws new failed`, e)
        retryTimer = setTimeout(connect, 3000)
        return
      }
      ws.onmessage = (ev) => {
        try {
          const data: DetectionFrame = JSON.parse(ev.data)
          latestRef.current = data
        } catch {}
      }
      ws.onerror = () => { /* onclose sẽ trigger retry */ }
      ws.onclose = () => {
        if (!closed) retryTimer = setTimeout(connect, 3000)
      }
    }
    connect()

    return () => {
      closed = true
      if (retryTimer) clearTimeout(retryTimer)
      if (ws && ws.readyState === WebSocket.OPEN) ws.close()
    }
  }, [sourceId, enabled])

  // ── Canvas draw loop (requestAnimationFrame) ───────────────────────────────
  useEffect(() => {
    if (!enabled) return
    const canvas = canvasRef.current
    const video  = videoRef.current
    if (!canvas || !video) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const draw = () => {
      // Resize canvas theo video element hiện tại (DPI-aware)
      const rect  = video.getBoundingClientRect()
      const cssW  = rect.width
      const cssH  = rect.height
      if (cssW === 0 || cssH === 0) {
        rafRef.current = requestAnimationFrame(draw)
        return
      }
      const dpr = window.devicePixelRatio || 1
      const canvasW = Math.round(cssW * dpr)
      const canvasH = Math.round(cssH * dpr)
      if (canvas.width !== canvasW || canvas.height !== canvasH) {
        canvas.width  = canvasW
        canvas.height = canvasH
        canvas.style.width  = `${cssW}px`
        canvas.style.height = `${cssH}px`
      }

      ctx.clearRect(0, 0, canvasW, canvasH)

      const frame = latestRef.current
      if (!frame || !frame.detections?.length) {
        rafRef.current = requestAnimationFrame(draw)
        return
      }

      // Drop detection cũ
      const ageSec = Date.now() / 1000 - frame.ts
      if (ageSec > maxAgeSec) {
        rafRef.current = requestAnimationFrame(draw)
        return
      }

      // Resolve frame dimensions. Backend đôi khi trả frame_w=0 (Savant
      // source_frame_width chưa populate) → fallback video.videoWidth/Height.
      const frameW = frame.frame_w || video.videoWidth  || 0
      const frameH = frame.frame_h || video.videoHeight || 0
      if (frameW === 0 || frameH === 0) {
        rafRef.current = requestAnimationFrame(draw)
        return
      }

      // Scale bbox từ frame gốc → canvas (theo object-fit: cover của video).
      // Cover: ảnh fill canvas không méo — cạnh nhỏ hơn của ảnh sẽ bị crop.
      const scaleX = canvasW / frameW
      const scaleY = canvasH / frameH
      const scale  = Math.max(scaleX, scaleY)   // cover
      const offX   = (canvasW - frameW * scale) / 2
      const offY   = (canvasH - frameH * scale) / 2

      ctx.textBaseline = 'top'

      // Sort: vẽ person trước (layer dưới), face sau (layer trên, đè lên)
      const sorted = [...frame.detections].sort((a, b) => {
        const ra = a.label === 'face' ? 1 : 0
        const rb = b.label === 'face' ? 1 : 0
        return ra - rb
      })

      for (const det of sorted) {
        if (!det.bbox || det.bbox.length !== 4) continue
        const [x1, y1, x2, y2] = det.bbox
        const cx1 = x1 * scale + offX
        const cy1 = y1 * scale + offY
        const cx2 = x2 * scale + offX
        const cy2 = y2 * scale + offY
        const w = cx2 - cx1
        const h = cy2 - cy1

        const isFace    = det.label === 'face'
        const isVehicle = !!det.plate_number
        const hasIdent  = !!det.person_id

        // Pick color + label + style
        let color = '#94a3b8'   // default xám nhạt cho person/car raw
        let label = det.label
        let dashed = false
        let lineW  = 2 * dpr
        let fontSz = 12 * dpr

        if (isFace) {
          // Khung mặt — luôn đè lên, line đậm, màu theo role/stranger
          lineW  = 3 * dpr
          fontSz = 13 * dpr
          if (hasIdent) {
            color = ROLE_COLOR[det.person_role || 'unknown'] || ROLE_COLOR.unknown
            label = det.person_name
              ? `${det.person_name}${det.fr_confidence ? ` ${Math.round(det.fr_confidence * 100)}%` : ''}`
              : (String(det.person_id).toLowerCase() === 'stranger' ? '? Stranger' : String(det.person_id))
          } else {
            color = '#f59e0b'   // vàng — face detect được nhưng chưa match
            label = 'đang nhận diện…'
          }
        } else if (isVehicle) {
          color = '#06b6d4'
          label = `🚘 ${det.plate_number}${det.plate_category ? ` [${det.plate_category}]` : ''}`
          lineW = 3 * dpr
        } else {
          // Person/car/motorcycle/... raw từ Stage 1 — khung MỜ (dashed, line mảnh)
          // để người dùng hiểu đây là bbox body chứ không phải identity.
          dashed = true
          lineW  = 1.5 * dpr
          fontSz = 10 * dpr
          label  = det.label
        }

        ctx.lineWidth = lineW
        ctx.font      = `600 ${fontSz}px system-ui, sans-serif`
        ctx.strokeStyle = color

        if (dashed) ctx.setLineDash([6 * dpr, 4 * dpr])
        else        ctx.setLineDash([])
        ctx.strokeRect(cx1, cy1, w, h)
        ctx.setLineDash([])

        // Label background (chỉ vẽ label cho face + vehicle — person raw không cần)
        if (!dashed) {
          const text  = label
          const textW = ctx.measureText(text).width
          const padX  = 6 * dpr
          const padY  = 3 * dpr
          const bgH   = fontSz + padY * 2
          ctx.fillStyle = color
          ctx.fillRect(cx1, Math.max(0, cy1 - bgH), textW + padX * 2, bgH)
          ctx.fillStyle = '#ffffff'
          ctx.fillText(text, cx1 + padX, Math.max(0, cy1 - bgH) + padY)
        }
      }

      rafRef.current = requestAnimationFrame(draw)
    }
    rafRef.current = requestAnimationFrame(draw)

    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    }
  }, [enabled, videoRef, maxAgeSec])

  if (!enabled) return null

  return (
    <canvas
      ref={canvasRef}
      style={{
        position:      'absolute',
        inset:         0,
        pointerEvents: 'none',
        width:         '100%',
        height:        '100%',
      }}
    />
  )
}
