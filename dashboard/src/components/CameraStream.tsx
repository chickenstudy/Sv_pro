/**
 * CameraStream — Live video player cho SV-PRO Dashboard.
 *
 * Kết nối trực tiếp go2rtc:
 *   1. HLS (hls.js) — latency thấp, ổn định nhất, tương thích rộng
 *   2. WebRTC — qua go2rtc JS client (latency ~200ms)
 *
 * Stream URL lấy từ: GET /api/stream/{cam_id}/info
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import { RefreshCw, Wifi, WifiOff, X, Grid2x2, Square, Monitor } from 'lucide-react'
import Hls from 'hls.js'
import type HlsType from 'hls.js'
import { streamApi, type Camera } from '../api'
import { DetectionOverlay } from './DetectionOverlay'

// ── go2rtc helpers ────────────────────────────────────────────────────────────

function getGo2rtcBase(): string {
  // Dev: proxy /go2rtc-api → localhost:1984
  // Prod: VITE_GO2RTC_URL chỉ định thẳng
  const envUrl = import.meta.env.VITE_GO2RTC_URL as string | undefined
  if (envUrl) return envUrl
  const proto = location.protocol === 'https:' ? 'https:' : 'http:'
  return `${proto}//${location.host}/go2rtc-api`
}

function buildUrls(base: string, srcId: string) {
  // Các endpoint stream của go2rtc nằm dưới /api/. base ở đây là proxy
  // path (mặc định "/go2rtc-api") → URL cuối qua nginx rewrite về go2rtc:1984.
  return {
    webrtc:    `${base}/api/webrtc?src=${srcId}`,
    hls:       `${base}/api/stream.m3u8?src=${srcId}`,
    mse:       `${base}/api/stream.mp4?src=${srcId}`,
    rtsp:      `${base}/api/rtsp/stream?src=${srcId}`,
    player_ui: `${base}/?src=${srcId}`,
  }
}

// ── CameraTile ─────────────────────────────────────────────────────────────────

type TileStatus = 'idle' | 'loading' | 'live' | 'error' | 'offline'

interface CameraTileProps {
  camera: Camera
  compact?: boolean
}

export function CameraTile({ camera, compact }: CameraTileProps) {
  const videoRef    = useRef<HTMLVideoElement>(null)
  const hlsRef      = useRef<HlsType | null>(null)
  const pcRef       = useRef<RTCPeerConnection | null>(null)
  const retryTimer  = useRef<ReturnType<typeof setTimeout> | null>(null)
  const go2rtcBase  = getGo2rtcBase()

  const [status, setStatus] = useState<TileStatus>(camera.enabled ? 'idle' : 'offline')
  const [errMsg, setErrMsg] = useState('')
  const [sourceId, setSourceId] = useState<string>('')
  // Toggle overlay (default: true — hiện bbox detect). Click vào video để tắt/bật.
  const [showOverlay, setShowOverlay] = useState(true)

  const stopAll = useCallback(() => {
    if (hlsRef.current) { hlsRef.current.destroy(); hlsRef.current = null }
    if (pcRef.current) {
      try { pcRef.current.close() } catch {}
      pcRef.current = null
    }
    if (videoRef.current) {
      videoRef.current.pause()
      videoRef.current.srcObject = null
      videoRef.current.src = ''
    }
  }, [])

  const play = useCallback(async () => {
    if (!videoRef.current || !camera.enabled) return
    stopAll()
    if (retryTimer.current) clearTimeout(retryTimer.current)

    setStatus('loading')
    setErrMsg('')

    try {
      const info = await streamApi.getInfo(camera.id)
      setSourceId(info.source_id)

      // Ưu tiên 1: WebRTC qua go2rtc (latency thấp ~200ms — chính). Nếu fail
      // (network/firewall chặn UDP) → fallback HLS.
      try {
        const pc = await playWebRTC(videoRef.current, info.source_id, go2rtcBase)
        pcRef.current = pc
        setStatus('live')
        return
      } catch (wrtcErr) {
        console.warn(`[cam ${camera.id}] WebRTC failed, fallback HLS:`, wrtcErr)
      }

      // Fallback: HLS (latency cao 2-5s nhưng compatible Safari/iOS + qua firewall).
      const hlsUrl = `${go2rtcBase}/api/stream.m3u8?src=${encodeURIComponent(info.source_id)}`
      const ok = await playHLS(videoRef.current, hlsUrl)
      if (ok) { setStatus('live'); return }

      setStatus('error')
      setErrMsg('Stream không khả dụng. Cả WebRTC và HLS đều fail.')
      retryTimer.current = setTimeout(play, 5000)
    } catch (err: any) {
      console.error(`[CameraTile cam=${camera.id}]`, err)
      setStatus('error')
      setErrMsg(err?.message || 'Kết nối thất bại')
      retryTimer.current = setTimeout(play, 5000)
    }
  }, [camera.id, camera.enabled, go2rtcBase, stopAll])

  useEffect(() => {
    if (!camera.enabled) { setStatus('offline'); stopAll(); return }
    play()
    return () => {
      stopAll()
      if (retryTimer.current) clearTimeout(retryTimer.current)
    }
  }, [play, camera.enabled])

  const isLive    = status === 'live'
  const isLoading = status === 'loading'
  const isError  = status === 'error'
  const isOffline = status === 'offline'

  return (
    <div className="cam-tile" style={{ borderColor: isLive ? 'var(--success)50' : 'var(--border)' }}>
      <div className="cam-tile__video">
        {camera.enabled ? (
          <>
            <video
              ref={videoRef}
              className="cam-tile__canvas"
              autoPlay
              muted
              playsInline
              style={{ width: '100%', height: '100%', objectFit: 'cover', background: '#000' }}
            />
            {/* Overlay bbox detection realtime từ AI pipeline */}
            {isLive && sourceId && (
              <DetectionOverlay
                sourceId={sourceId}
                videoRef={videoRef}
                enabled={showOverlay}
              />
            )}
            {/* Toggle bbox overlay (nhỏ góc trên-phải) */}
            {isLive && (
              <button
                onClick={() => setShowOverlay(v => !v)}
                title={showOverlay ? 'Ẩn khung detect' : 'Hiện khung detect'}
                style={{
                  position: 'absolute', top: 6, right: 6, zIndex: 5,
                  background: showOverlay ? 'rgba(34,197,94,0.85)' : 'rgba(0,0,0,0.55)',
                  color: '#fff', border: 'none', padding: '3px 8px',
                  borderRadius: 6, cursor: 'pointer', fontSize: 10, fontWeight: 700,
                  backdropFilter: 'blur(4px)',
                }}
              >
                {showOverlay ? '⬛ AI' : '⬜ AI'}
              </button>
            )}
            {isLoading && (
              <div className="cam-tile__overlay cam-tile__overlay--loading">
                <RefreshCw size={22} className="animate-spin" />
                <span>Đang kết nối stream...</span>
              </div>
            )}
            {isError && (
              <div className="cam-tile__overlay cam-tile__overlay--error">
                <WifiOff size={22} />
                <span style={{ fontWeight: 600 }}>Stream lỗi</span>
                <span style={{ fontSize: 10, opacity: 0.7 }}>{errMsg || 'Tự kết nối lại sau 5s'}</span>
                <button
                  className="btn btn--sm"
                  style={{ marginTop: 4, background: 'rgba(255,255,255,0.1)' }}
                  onClick={() => { if (retryTimer.current) clearTimeout(retryTimer.current); play() }}
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

      <div className="cam-tile__info">
        <div className="cam-tile__name">
          {isLive && <span className="dot-live" style={{ width: 6, height: 6, flexShrink: 0 }} />}
          <span style={{ fontWeight: 600, fontSize: 12 }}>{camera.name}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span className="text-muted text-sm" style={{ fontSize: 11 }}>
            {camera.location || camera.zone || '—'}
          </span>
          <span
            className={`badge badge--${
              isLive ? 'success' : isLoading ? 'info' : isError ? 'danger' : 'low'
            }`}
            style={{ fontSize: 10 }}
          >
            {isLive ? 'LIVE' : isLoading ? 'CONNECTING' : isError ? 'ERROR' : 'OFF'}
          </span>
        </div>
      </div>
    </div>
  )
}

// ── CameraGrid ─────────────────────────────────────────────────────────────────

export type LayoutMode = 1 | 2 | 4 | 9

interface CameraGridProps {
  cameras: Camera[]
}

const LAYOUT_OPTIONS: { mode: LayoutMode; icon: React.ElementType; label: string }[] = [
  { mode: 1, icon: Monitor,  label: '1 camera' },
  { mode: 2, icon: Square,  label: '2 camera' },
  { mode: 4, icon: Grid2x2, label: '4 camera (2x2)' },
  { mode: 9, icon: Grid2x2, label: '9 camera (3x3)' },
]

function gridStyle(count: number): React.CSSProperties {
  if (count === 1) return { gridTemplateColumns: '1fr' }
  if (count <= 2)  return { gridTemplateColumns: 'repeat(2, 1fr)' }
  if (count <= 4)  return { gridTemplateColumns: 'repeat(2, 1fr)' }
  return { gridTemplateColumns: 'repeat(3, 1fr)' }
}

export function CameraGrid({ cameras }: CameraGridProps) {
  const [shown,  setShown]  = useState<number[]>([])
  const [layout, setLayout] = useState<LayoutMode>(4)

  const enabledCams = cameras.filter(c => c.enabled)

  // Auto-select first N cameras when list changes
  useEffect(() => {
    if (cameras.length === 0) { setShown([]); return }
    if (shown.length === 0 || !shown.some(id => enabledCams.some(c => c.id === id))) {
      setShown(enabledCams.slice(0, layout).map(c => c.id))
    }
  }, [cameras.length, layout])

  const toggleCam = (id: number) => {
    if (shown.includes(id)) {
      setShown(s => s.filter(x => x !== id))
    } else if (shown.length < 9) {
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

  const visibleCams = cameras.filter(c => shown.includes(c.id) && c.enabled)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Toolbar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        {/* Camera pills */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', flex: 1 }}>
          {enabledCams.map(c => (
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

        {/* Layout picker */}
        <div style={{
          display: 'flex', gap: 4,
          background: 'var(--bg-elevated)',
          padding: 3,
          borderRadius: 'var(--r-md)',
          border: '1px solid var(--border)',
        }}>
          {LAYOUT_OPTIONS.map(({ mode, icon: Icon, label }) => (
            <button
              key={mode}
              className={`btn btn--sm ${layout === mode ? 'btn--primary' : 'btn--ghost'}`}
              onClick={() => {
                setLayout(mode)
                if (shown.length > mode) setShown(s => s.slice(0, mode))
              }}
              title={label}
            >
              <Icon size={12} />
            </button>
          ))}
        </div>

        <span style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
          {visibleCams.length}/{enabledCams.length} camera
        </span>
      </div>

      {/* Grid */}
      {visibleCams.length === 0 ? (
        <div className="empty-state">
          <Wifi size={36} className="empty-state__icon" />
          Chọn camera để xem video trực tiếp
        </div>
      ) : (
        <div style={{ display: 'grid', ...gridStyle(visibleCams.length), gap: 12 }}>
          {visibleCams.map(cam => (
            <CameraTile key={cam.id} camera={cam} compact={visibleCams.length >= 2} />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Player internals ───────────────────────────────────────────────────────────

function playHLS(video: HTMLVideoElement, url: string): Promise<boolean> {
  return new Promise(resolve => {
    if (!Hls || !Hls.isSupported()) {
      // Safari/iOS: native HLS
      if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = url
        video.play().catch(() => {})
        resolve(true)
        return
      }
      resolve(false)
      return
    }

    // HLS dùng làm fallback khi WebRTC fail. Tắt lowLatencyMode để buffer
    // 3-4 segment, ít rebuffer hơn — đánh đổi latency cao hơn (3-5s).
    const hls = new Hls({
      enableWorker:        true,
      lowLatencyMode:      false,
      backBufferLength:    30,
      maxBufferLength:     10,
      maxMaxBufferLength:  20,
      liveSyncDurationCount: 3,
    })
    hlsRefGlobal = hls

    hls.loadSource(url)
    hls.attachMedia(video)

    hls.on(Hls.Events.ERROR, (_: any, data: any) => {
      if (data.fatal) { hls.destroy(); hlsRefGlobal = null; resolve(false) }
    })
    hls.on(Hls.Events.FRAG_LOADED, () => { video.play().catch(() => {}) })

    setTimeout(() => resolve(true), 2000)
  })
}

let hlsRefGlobal: HlsType | null = null

/**
 * WebRTC qua go2rtc — giao thức POST SDP đơn giản (không cần WebSocket).
 *
 * Flow chuẩn theo go2rtc docs:
 *   1. Tạo PC, addTransceiver recvonly cho video + audio.
 *   2. createOffer → setLocalDescription.
 *   3. Wait ICE gathering complete (lấy đủ candidate vào SDP).
 *   4. POST sdp text/plain → /api/webrtc?src=ID → nhận answer SDP.
 *   5. setRemoteDescription(answer) → ontrack fire → gắn srcObject.
 *
 * Trả về RTCPeerConnection để caller cleanup khi unmount.
 */
async function playWebRTC(
  video: HTMLVideoElement,
  sourceId: string,
  base: string,
): Promise<RTCPeerConnection> {
  const pc = new RTCPeerConnection({
    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
  })

  // Recvonly: chỉ nhận, không gửi
  pc.addTransceiver('video', { direction: 'recvonly' })
  pc.addTransceiver('audio', { direction: 'recvonly' })

  // Promise wait ontrack
  const waitTrack = new Promise<void>((resolve) => {
    pc.ontrack = (e) => {
      video.srcObject = e.streams[0]
      video.play().catch(() => {})
      resolve()
    }
  })

  // Tạo offer
  const offer = await pc.createOffer()
  await pc.setLocalDescription(offer)

  // Đợi ICE gathering (tối đa 1.5s — tránh hang)
  await new Promise<void>((resolve) => {
    if (pc.iceGatheringState === 'complete') return resolve()
    const t = setTimeout(resolve, 1500)
    const check = () => {
      if (pc.iceGatheringState === 'complete') {
        clearTimeout(t)
        pc.removeEventListener('icegatheringstatechange', check)
        resolve()
      }
    }
    pc.addEventListener('icegatheringstatechange', check)
  })

  // POST SDP offer → nhận answer
  const url = `${base}/api/webrtc?src=${encodeURIComponent(sourceId)}`
  const resp = await fetch(url, {
    method:  'POST',
    headers: { 'Content-Type': 'application/sdp' },
    body:    pc.localDescription?.sdp || '',
  })
  if (!resp.ok) {
    pc.close()
    throw new Error(`WebRTC HTTP ${resp.status}`)
  }
  const answerSdp = await resp.text()
  if (!answerSdp.startsWith('v=')) {
    pc.close()
    throw new Error('WebRTC: invalid SDP answer')
  }
  await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp })

  // Race: ontrack hoặc timeout 8s
  await Promise.race([
    waitTrack,
    new Promise<void>((_, reject) =>
      setTimeout(() => reject(new Error('WebRTC track timeout')), 8000),
    ),
  ])

  return pc
}
