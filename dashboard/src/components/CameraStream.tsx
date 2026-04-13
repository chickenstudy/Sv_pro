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
import type HlsType from 'hls.js'
import { streamApi, type Camera } from '../api'

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
  return {
    webrtc:    `${base}/webrtc?src=${srcId}`,
    hls:       `${base}/stream.m3u8?src=${srcId}`,
    mse:       `${base}/stream.mp4?src=${srcId}`,
    rtsp:      `${base}/rtsp/stream?src=${srcId}`,
    player_ui: `${base}/?src=${srcId}`,
  }
}

// ── go2rtc WebRTC client (lazy load từ CDN) ────────────────────────────────────

let _go2rtcLoaded = false
let _go2rtcInstance: any = null

async function getGo2rtc(): Promise<any> {
  if (_go2rtcInstance) return _go2rtcInstance
  if (_go2rtcLoaded) throw new Error('go2rtc not available')

  await new Promise<void>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = 'https://cdn.jsdelivr.net/npm/go2rtc@latest/build/go2rtc-api.js'
    script.onload = () => { _go2rtcLoaded = true; resolve() }
    script.onerror = () => reject(new Error('Failed to load go2rtc'))
    document.head.appendChild(script)
  })

  const base = getGo2rtcBase().replace(/^http/, 'ws').replace(/\/$/, '')
  _go2rtcInstance = new (window as any).Go2rtc({})
  return _go2rtcInstance
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
  const retryTimer   = useRef<ReturnType<typeof setTimeout> | null>(null)
  const go2rtcBase  = getGo2rtcBase()

  const [status, setStatus] = useState<TileStatus>(camera.enabled ? 'idle' : 'offline')
  const [errMsg, setErrMsg] = useState('')

  const stopAll = useCallback(() => {
    if (hlsRef.current) { hlsRef.current.destroy(); hlsRef.current = null }
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
      const urls = buildUrls(go2rtcBase, info.source_id)

      // Ưu tiên 1: HLS (ổn định nhất, tương thích rộng)
      if (urls.hls) {
        const ok = await playHLS(videoRef.current, urls.hls)
        if (ok) { setStatus('live'); return }
      }

      // Ưu tiên 2: WebRTC qua go2rtc client
      if (urls.webrtc) {
        try {
          await playWebRTC(videoRef.current, info.source_id, getGo2rtcBase())
          setStatus('live'); return
        } catch { /* continue to fallback */ }
      }

      setStatus('error')
      setErrMsg('Stream không khả dụng. Kiểm tra camera và go2rtc.')
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
    const Hls = (window as any).Hls
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

    const hls = new Hls({ enableWorker: true, lowLatencyMode: true })
    hlsRefGlobal = hls

    hls.loadSource(url)
    hls.attachMedia(video)

    hls.on('hlsError', (_: any, data: any) => {
      if (data.fatal) { hls.destroy(); hlsRefGlobal = null; resolve(false) }
    })
    hls.on('hlsFragLoaded', () => { video.play().catch(() => {}) })

    setTimeout(() => resolve(true), 2000)
  })
}

let hlsRefGlobal: HlsType | null = null

async function playWebRTC(video: HTMLVideoElement, sourceId: string, base: string): Promise<void> {
  return new Promise(async (resolve, reject) => {
    try {
      const go2rtc = await getGo2rtc()
      const pc = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
      })

      pc.ontrack = (e: any) => {
        video.srcObject = e.streams[0]
        video.play().catch(() => {})
        resolve()
      }
      pc.oniceconnectionstatechange = () => {
        if (['failed', 'disconnected', 'closed'].includes(pc.iceConnectionState)) {
          pc.close()
          reject(new Error('WebRTC disconnected'))
        }
      }

      // go2rtc WebRTC: tạo offer, gửi cho go2rtc, nhận answer
      const offer = await pc.createOffer({ offerToReceiveAudio: false, offerToReceiveVideo: true })
      await pc.setLocalDescription(offer)

      const wsUrl = `${base.replace(/^http/, 'ws')}/webrtc`
      const ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'offer', src: sourceId, sdp: pc.localDescription?.sdp }))
      }

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          if (msg.type === 'answer') {
            pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: msg.sdp }))
            ws.close()
          } else if (msg.candidate) {
            pc.addIceCandidate(new RTCIceCandidate(msg))
          }
        } catch {}
      }

      ws.onerror = () => { ws.close(); reject(new Error('WebSocket error')) }
      setTimeout(() => {
        if (video.srcObject) resolve()
        else reject(new Error('WebRTC timeout'))
      }, 8000)
    } catch (e) { reject(e) }
  })
}
