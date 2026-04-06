import { useState, useEffect } from 'react'
import { doorsApi, type Door } from '../api'

/**
 * Trang Quản lý Cửa (Access Control) — hiển thị danh sách các Relay cửa,
 * trạng thái hiện tại, thời gian mở, và cho phép ADMIN Kích hoạt Cửa khẩn cấp.
 */
export default function DoorsPage() {
  const [doors, setDoors] = useState<Door[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    try {
      setLoading(true)
      const data = await doorsApi.list()
      setDoors(data)
      setError(null)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  // Bật/tắt trạng thái Cửa
  const handleToggle = async (doorId: string, currentStatus: boolean) => {
    const confirmation = currentStatus 
      ? `Bạn có chắc chắn muốn VÔ HIỆU HÓA cửa [${doorId}]? (Cửa sẽ không tự động mở khi có người đi vào)`
      : `Bật lại hệ thống cửa [${doorId}]?`
    if (!window.confirm(confirmation)) return

    try {
      await doorsApi.toggle(doorId, !currentStatus)
      await load() // tải lại danh sách
    } catch (e: any) {
      alert(`Lỗi: ${e.message}`)
    }
  }

  // Mở cửa khẩn cấp (Manual Trigger)
  const handleTrigger = async (doorId: string) => {
    if (!window.confirm(`XÁC NHẬN MỞ CỬA KHẨN CẤP [${doorId}]?`)) return
    
    try {
      const res = await doorsApi.trigger(doorId)
      if (res.granted) {
        alert(`🔓 MỞ CỬA THÀNH CÔNG: ${res.reason}\n🕒 Độ trễ Relay: ${(res as any).latency_ms?.toFixed(0) || '< 1'}ms`)
      } else {
        alert(`❌ TỪ CHỐI MỞ CỬA: ${res.reason}`)
      }
    } catch (e: any) {
      alert(`Lỗi Trigger Cửa. (Ghi chú: Nếu Server yêu cầu X-API-Key thì trình duyệt không gọi thẳng API này được).\nChi tiết lỗi: ${e.message}`)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700 }}>🚪 Quản lý Cửa & Relay</h2>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
            Xem trạng thái các cụm Relay, bật/tắt khả năng truy cập và kích hoạt mở cửa thủ công.
          </p>
        </div>
        <button className="btn btn--secondary" onClick={load}>🔄 Làm mới</button>
      </div>

      {error && (
        <div style={{ color: 'var(--danger)', fontSize: 12, padding: '8px 12px',
          background: 'var(--danger)15', borderRadius: 'var(--r-md)', border: '1px solid var(--danger)30' }}>
          ❌ {error}
        </div>
      )}

      {/* Grid danh sách cửa */}
      <div className="grid-2">
        {loading ? (
          <div className="empty-state" style={{ gridColumn: '1 / -1' }}>
            <div className="empty-state__icon">⏳</div>Đang tải danh sách cửa...
          </div>
        ) : doors.length === 0 ? (
          <div className="empty-state" style={{ gridColumn: '1 / -1' }}>
            <div className="empty-state__icon">🚪</div>Hệ thống chưa có Cửa nào được cấu hình.
          </div>
        ) : (
          doors.map(door => (
            <div key={door.door_id} className="card" style={{ 
              border: door.enabled ? '1px solid var(--border)' : '1px solid var(--danger)50',
              opacity: door.enabled ? 1 : 0.8
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                    {door.enabled ? '🟢' : '🔴'} {door.name}
                  </h3>
                  <code style={{ fontSize: 11, color: 'var(--text-muted)' }}>ID: {door.door_id}</code>
                </div>
                <span className={`badge ${door.enabled ? 'badge--success' : 'badge--danger'}`}>
                  {door.enabled ? 'BÌNH THƯỜNG' : 'BỊ KHÓA'}
                </span>
              </div>

              <div style={{ margin: '16px 0', display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                  <span style={{ color: 'var(--text-muted)' }}>Phân khu (Zone):</span>
                  <strong>{door.zone || 'N/A'}</strong>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                  <span style={{ color: 'var(--text-muted)' }}>Relay URL:</span>
                  <span className="font-mono text-muted text-sm truncate" style={{ maxWidth: 200 }}>{door.relay_url}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                  <span style={{ color: 'var(--text-muted)' }}>Thời gian ngàm mở:</span>
                  <span className="font-mono">{door.open_ms} ms</span>
                </div>
              </div>

              <div style={{ display: 'flex', gap: 12, marginTop: 24 }}>
                <button
                  className="btn btn--primary"
                  style={{ flex: 1, background: 'var(--warning)', color: '#000' }}
                  onClick={() => handleTrigger(door.door_id)}
                  disabled={!door.enabled}
                >
                  🔓 Mở Cửa Ngay
                </button>
                <button
                  className="btn btn--secondary"
                  onClick={() => handleToggle(door.door_id, door.enabled)}
                >
                  {door.enabled ? '🚫 Khóa Cửa' : '✅ Bật Lại'}
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
