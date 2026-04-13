/**
 * CameraStream — Live video player cho SV-PRO Dashboard.
 *
 * Kết nối WebSocket: ws://host/ws/stream/{camera_id}
 * jsmpeg decode MPEG-TS binary frames → canvas video playback.
 *
 * jsmpeg được load động từ CDN: https://cdn.jsdelivr.net/npm/jsmpeg@2.2.1/jsmpeg.min.js
 * Sau khi load, global `window.JSMpeg` sẽ có class `Player`.
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import { RefreshCw, Wifi, WifiOff, X } from 'lucide-react'
import type { Camera } from '../api'

// Định nghĩa kiểu cho jsmpeg global
declare global {
  interface Window {
    JSMpeg?: {
      Player: new (url: string, options?: any) => JSMpegPlayer
    }
  }
}

interface JSMpegPlayer {
  destroy(): void
  on(event: string, cb: (e: any) => void): void
}

function getWsUrl(cameraId: number): string {
  const loc = window.location
  const proto = loc.protocol === 'https:' ? 'wss' : 'ws'
  const host = loc.host
  return `${proto}://${host}/ws/stream/${cameraId}`
}

// Load jsmpeg từ CDN (browser only, gọi 1 lần duy nhất)
let _jsmpegLoaded = false
let _jsmpegPromise: Promise<boolean> | null = null

function loadJsmpeg(): Promise<boolean> {
  if (typeof window === 'undefined') return Promise.reject()
  if (_jsmpegLoaded && window.JSMpeg) return Promise.resolve(true)
  if (_jsmpegPromise) return _jsmpegPromise

  _jsmpegPromise = new Promise((resolve, reject) => {
    if (window.JSMpeg) {
      _jsmpegLoaded = true
      resolve(true)
      return
    }
    const script = document.createElement('script')
    script.src = 'https://cdn.jsdelivr.net/npm/jsmpeg@2.2.1/jsmpeg.min.js'
    script.onload = () => { _jsmpegLoaded = true; resolve(true) }
    script.onerror = () => reject(new Error('Failed to load jsmpeg'))
    document.head.appendChild(script)
  })
  return _jsmpegPromise
}

// ── CameraTile: 1 video player nhỏ ───────────────────────────────────────────

interface CameraTileProps {
  camera: Camera
  compact?: boolean
}

export function CameraTile({ camera, compact }: CameraTileProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const playerRef = useRef<JSMpegPlayer | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const [status, setStatus] = useState<'idle' | 'connecting' | 'live' | 'error' | 'disabled'>(
    camera.enabled ? 'idle' : 'disabled',
  )
  const [errorMsg, setErrorMsg] = useState('')

  const connect = useCallback(async () => {
    if (!canvasRef.current || !camera.enabled) return
    if (!window.JSMpeg) {
      try { await loadJsmpeg() } catch { /* ignore */ }
    }
    if (!window.JSMpeg) {
      setStatus('error')
      setErrorMsg('Không load được jsmpeg')
      return
    }

    setStatus('connecting')
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current)

    // Cleanup previous player
    if (playerRef.current) {
      try { playerRef.current.destroy() } catch (_) {}
      playerRef.current = null
    }

    try {
      const player = new window.JSMpeg.Player!(getWsUrl(camera.id), {
        canvas: canvasRef.current,
        audio: false,
        autoplay: true,
        loop: false,
        throttled: true,
      })

      player.on('error', (e: any) => {
        console.warn(`[CameraStream cam=${camera.id}] stream error:`, e)
        setStatus('error')
        setErrorMsg('Stream lỗi, thử kết nối lại...')
        reconnectTimer.current = setTimeout(connect, 5000)
      })

      playerRef.current = player
      setStatus('live')
      setErrorMsg('')
    } catch (e: any) {
      console.error(`[CameraStream cam=${camera.id}] connect error:`, e)
      setStatus('error')
      setErrorMsg(e?.message || 'Kết nối thất bại')
      reconnectTimer.current = setTimeout(connect, 5000)
    }
  }, [camera.id, camera.enabled])

  useEffect(() => {
    if (!camera.enabled) {
      setStatus('disabled')
      return
    }
    // Khởi tạo canvas dimensions trước khi connect
    if (canvasRef.current) {
      canvasRef.current.width = compact ? 320 : 640
      canvasRef.current.height = compact ? 180 : 360
    }
    connect()

    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      if (playerRef.current) {
        try { playerRef.current.destroy() } catch (_) {}
        playerRef.current = null
      }
    }
  }, [connect, camera.enabled, camera.id])

  const isLive = status === 'live'
  const isConnecting = status === 'connecting'
  const isError = status === 'error'

  return (
    <div className="cam-tile" style={{ borderColor: isLive ? 'var(--success)50' : 'var(--border)' }}>
      {/* Video area */}
      <div className="cam-tile__video">
        {camera.enabled ? (
          <>
            <canvas ref={canvasRef} className="cam-tile__canvas" />
            {isConnecting && (
              <div className="cam-tile__overlay cam-tile__overlay--loading">
                <RefreshCw size={22} className="animate-spin" />
                <span>Đang kết nối...</span>
              </div>
            )}
            {isError && (
              <div className="cam-tile__overlay cam-tile__overlay--error">
                <WifiOff size={22} />
                <span style={{ fontWeight: 600 }}>Stream lỗi</span>
                <span style={{ fontSize: 10, opacity: 0.7 }}>{errorMsg || 'Tự kết nối lại sau 5s'}</span>
                <button
                  className="btn btn--sm"
                  style={{ marginTop: 4, background: 'rgba(255,255,255,0.1)' }}
                  onClick={() => { if (reconnectTimer.current) clearTimeout(reconnectTimer.current); connect() }}
                >
                  <RefreshCw size={11} /> Thử lại
                </button>
              </div>
            )}
          </>
        ) : (
          <div className="cam-tile__overlay cam-tile__overlay--disabled">
            <X size={28} />
            <span>Camera tắt</span>
          </div>
        )}
      </div>

      {/* Info bar */}
      <div className="cam-tile__info">
        <div className="cam-tile__name">
          {isLive && <span className="dot-live" style={{ width: 6, height: 6, flexShrink: 0 }} />}
          <span style={{ fontWeight: 600, fontSize: 12 }}>{camera.name}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span className="text-muted text-sm" style={{ fontSize: 11 }}>
            {camera.location || camera.zone || '—'}
          </span>
          <span className={`badge badge--${isLive ? 'success' : isConnecting ? 'info' : 'low'}`} style={{ fontSize: 10 }}>
            {isLive ? 'LIVE' : isConnecting ? 'CONNECTING' : camera.enabled ? 'IDLE' : 'OFF'}
          </span>
        </div>
      </div>
    </div>
  )
}

// ── CameraGrid: grid video cho Dashboard ─────────────────────────────────────

interface CameraGridProps {
  cameras: Camera[]
}

export function CameraGrid({ cameras }: CameraGridProps) {
  const [shown, setShown] = useState<number[]>([])

  // Auto-show first 4 enabled cameras
  useEffect(() => {
    const enabled = cameras.filter(c => c.enabled).slice(0, 4)
    setShown(enabled.map(c => c.id))
  }, [cameras.length])

  const visibleCams = cameras.filter(c => shown.includes(c.id) && c.enabled)

  const toggleCam = (id: number) => {
    if (shown.includes(id)) {
      setShown(s => s.filter(x => x !== id))
    } else if (shown.length < 4) {
      setShown(s => [...s, id])
    }
  }

  if (cameras.length === 0) {
    return (
      <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
        Chưa có camera nào. Thêm camera trong mục "Camera".
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Camera selector tabs */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {cameras.filter(c => c.enabled).map(c => (
          <button
            key={c.id}
            className={`btn btn--sm ${shown.includes(c.id) ? 'btn--primary' : 'btn--ghost'}`}
            onClick={() => toggleCam(c.id)}
          >
            <Wifi size={11} />
            {c.name}
          </button>
        ))}
      </div>

      {/* Video grid */}
      {visibleCams.length === 0 ? (
        <div className="empty-state">
          <Wifi size={36} className="empty-state__icon" />
          Chọn camera để xem video trực tiếp
        </div>
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: visibleCams.length === 1
              ? '1fr'
              : 'repeat(2, 1fr)',
            gap: 16,
          }}
        >
          {visibleCams.map(cam => (
            <CameraTile key={cam.id} camera={cam} compact={visibleCams.length >= 2} />
          ))}
        </div>
      )}
    </div>
  )
}
