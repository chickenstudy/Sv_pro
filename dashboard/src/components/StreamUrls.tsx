/**
 * StreamUrls — Hiển thị các link stream của camera với nút copy/open.
 * Dùng cho Dashboard khi user muốn lấy URL để xem bằng player khác.
 */

import { useState } from 'react'
import { Link2, Copy, Check, ExternalLink } from 'lucide-react'

interface StreamUrlsProps {
  urls: {
    webrtc?:    string
    hls?:       string
    mse?:       string
    rtsp?:      string
    player_ui?: string
  }
  compact?: boolean
}

const PROTOCOL_LABELS: Record<string, string> = {
  webrtc:   'WebRTC',
  hls:      'HLS',
  mse:      'MSE',
  rtsp:     'RTSP',
  player_ui: 'Player',
}

const PROTOCOL_COLORS: Record<string, string> = {
  webrtc:    'brand',
  hls:       'info',
  mse:       'success',
  rtsp:      'low',
  player_ui: 'brand',
}

export function StreamUrls({ urls, compact }: StreamUrlsProps) {
  const [copied, setCopied] = useState<string | null>(null)

  const handleCopy = async (key: string, url: string) => {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(key)
      setTimeout(() => setCopied(null), 2000)
    } catch {
      // fallback
      const ta = document.createElement('textarea')
      ta.value = url
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      setCopied(key)
      setTimeout(() => setCopied(null), 2000)
    }
  }

  const entries = (Object.entries(urls) as [string, string][])
    .filter(([, v]) => v)

  if (entries.length === 0) return null

  return (
    <div style={{
      display: 'flex',
      flexDirection: compact ? 'row' : 'column',
      gap: compact ? 4 : 8,
      flexWrap: 'wrap' as const,
      alignItems: compact ? 'center' : 'stretch',
    }}>
      {entries.map(([key, url]) => {
        const label = PROTOCOL_LABELS[key] ?? key
        const color = PROTOCOL_COLORS[key] ?? 'info'

        return (
          <div
            key={key}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '4px 8px',
              background: 'var(--bg-elevated)',
              borderRadius: 'var(--r-sm)',
              border: '1px solid var(--border)',
              fontSize: 11,
            }}
          >
            <span className={`badge badge--${color}`} style={{ fontSize: 10 }}>
              {label}
            </span>
            <code
              style={{
                fontSize: 10,
                color: 'var(--text-muted)',
                maxWidth: compact ? 180 : 300,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
              title={url}
            >
              {url}
            </code>
            <div style={{ display: 'flex', gap: 2 }}>
              <button
                className="btn btn--icon btn--ghost"
                style={{ padding: 2 }}
                title="Copy link"
                onClick={() => handleCopy(key, url)}
              >
                {copied === key ? (
                  <Check size={11} color="var(--success)" />
                ) : (
                  <Copy size={11} />
                )}
              </button>
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="btn btn--icon btn--ghost"
                style={{ padding: 2, textDecoration: 'none' }}
                title="Open in new tab"
              >
                <ExternalLink size={11} />
              </a>
            </div>
          </div>
        )
      })}
    </div>
  )
}