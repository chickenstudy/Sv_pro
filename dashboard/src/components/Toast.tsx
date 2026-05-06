import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from 'react'
import { CheckCircle, AlertTriangle, X, Info } from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────
export type ToastType = 'success' | 'error' | 'warning' | 'info'

export interface Toast {
  id: string
  type: ToastType
  message: string
  duration?: number  // ms, default 3500; 0 = sticky
}

interface ToastCtx {
  toasts: Toast[]
  show: (type: ToastType, message: string, duration?: number) => void
  dismiss: (id: string) => void
  success: (msg: string, duration?: number) => void
  error:   (msg: string, duration?: number) => void
  warn:    (msg: string, duration?: number) => void
  info:    (msg: string, duration?: number) => void
}

// ── Context ───────────────────────────────────────────────────────────────────
const Ctx = createContext<ToastCtx | null>(null)

export function useToast() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useToast must be inside ToastProvider')
  return ctx
}

// ── Single Toast ──────────────────────────────────────────────────────────────
const COLORS: Record<ToastType, { border: string; icon: string; bg: string }> = {
  success: { border: 'var(--success)', icon: 'var(--success)', bg: 'var(--success-glow)' },
  error:   { border: 'var(--danger)',  icon: 'var(--danger)',  bg: 'var(--danger-glow)' },
  warning: { border: 'var(--warning)', icon: 'var(--warning)', bg: 'rgba(245,158,11,.08)' },
  info:    { border: 'var(--brand)',   icon: 'var(--brand)',   bg: 'var(--brand-glow)' },
}

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  const [visible, setVisible] = useState(false)
  const col = COLORS[toast.type]

  useEffect(() => {
    // Mount animation
    const t = setTimeout(() => setVisible(true), 10)
    return () => clearTimeout(t)
  }, [])

  useEffect(() => {
    if (!toast.duration && toast.duration !== 0) return
    if (toast.duration === 0) return  // sticky
    const t = setTimeout(onDismiss, toast.duration)
    return () => clearTimeout(t)
  }, [toast.duration, onDismiss])

  const Icon = toast.type === 'success' ? CheckCircle
             : toast.type === 'error'   ? AlertTriangle
             : toast.type === 'warning' ? AlertTriangle
             : Info

  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 10,
      padding: '10px 12px',
      background: col.bg,
      border: `1px solid ${col.border}`,
      borderRadius: 'var(--r-md)',
      boxShadow: '0 8px 24px rgba(0,0,0,.4)',
      maxWidth: 320, minWidth: 220,
      transition: 'opacity .2s, transform .2s',
      opacity: visible ? 1 : 0,
      transform: visible ? 'translateX(0)' : 'translateX(20px)',
      pointerEvents: 'all',
    }}>
      <Icon size={15} style={{ color: col.icon, flexShrink: 0, marginTop: 1 }} />
      <span style={{ fontSize: 12, flex: 1, lineHeight: 1.5, color: 'var(--text-primary)' }}>{toast.message}</span>
      <button onClick={onDismiss}
        style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: 2, flexShrink: 0 }}>
        <X size={13} />
      </button>
    </div>
  )
}

// ── Provider ──────────────────────────────────────────────────────────────────
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const dismiss = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  const show = useCallback((type: ToastType, message: string, duration = 3500) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
    setToasts(prev => [...prev.slice(-4), { id, type, message, duration }])
    if (duration > 0) setTimeout(() => dismiss(id), duration + 300)
  }, [dismiss])

  const success = useCallback((msg: string, d?: number) => show('success', msg, d), [show])
  const error   = useCallback((msg: string, d?: number) => show('error', msg, d ?? 5000), [show])
  const warn    = useCallback((msg: string, d?: number) => show('warning', msg, d), [show])
  const info    = useCallback((msg: string, d?: number) => show('info', msg, d), [show])

  return (
    <Ctx.Provider value={{ toasts, show, dismiss, success, error, warn, info }}>
      {children}
      {/* Toast container */}
      <div style={{
        position: 'fixed', bottom: 20, right: 20, zIndex: 9999,
        display: 'flex', flexDirection: 'column', gap: 8,
        alignItems: 'flex-end', pointerEvents: 'none',
      }}>
        {toasts.map(t => <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />)}
      </div>
    </Ctx.Provider>
  )
}
