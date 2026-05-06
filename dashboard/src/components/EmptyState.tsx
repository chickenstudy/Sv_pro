import { type ReactNode } from 'react'
import { type LucideIcon } from 'lucide-react'

interface Props {
  icon: LucideIcon
  title: string
  subtitle?: string
  action?: ReactNode
}

export function EmptyState({ icon: Icon, title, subtitle, action }: Props) {
  return (
    <div style={{ padding: '48px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8, textAlign: 'center' }}>
      <Icon size={40} strokeWidth={1} style={{ color: 'var(--text-muted)', opacity: 0.5 }} />
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>{title}</div>
      {subtitle && <div style={{ fontSize: 12, color: 'var(--text-muted)', maxWidth: 260 }}>{subtitle}</div>}
      {action && <div style={{ marginTop: 4 }}>{action}</div>}
    </div>
  )
}

// ── Skeleton loading card ─────────────────────────────────────────────────────
export function SkeletonCard({ lines = 3, height = 60 }: { lines?: number; height?: number }) {
  return (
    <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '12px 14px', height, display: 'flex', flexDirection: 'column', gap: 8, justifyContent: 'center' }}>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} style={{
          height: 10, borderRadius: 5, background: 'var(--bg-hover)',
          width: i === 0 ? '60%' : i % 2 === 0 ? '45%' : '80%',
          animation: 'shimmer 1.4s ease infinite',
          backgroundImage: 'linear-gradient(90deg, var(--bg-hover) 25%, var(--bg-surface) 50%, var(--bg-hover) 75%)',
          backgroundSize: '200% 100%',
        }} />
      ))}
    </div>
  )
}

// ── Inline loading spinner ────────────────────────────────────────────────────
export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" style={{ animation: 'spin 1s linear infinite' }}>
      <circle cx={12} cy={12} r={10} fill="none" stroke="var(--text-muted)" strokeWidth={2.5} strokeDasharray="31.4 10" strokeLinecap="round" />
    </svg>
  )
}
