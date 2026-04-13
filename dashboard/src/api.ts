// ─ SV-PRO API Client ─────────────────────────────────────────────────────────
// Tất cả request đến FastAPI backend đều đi qua module này.
// Tự động đính kèm JWT token từ localStorage vào header Authorization.

const BASE_URL = import.meta.env.VITE_API_URL || 'http://192.168.42.171:8000'  // '' = relative path, Nginx proxy /api/* → backend:8000

// ── Auth helpers ──────────────────────────────────────────────────────────────

/** Lấy JWT token đang lưu trong localStorage. */
export const getToken = (): string | null => localStorage.getItem('svpro_token')

/** Lưu JWT token sau khi đăng nhập thành công. */
export const setToken = (token: string): void => localStorage.setItem('svpro_token', token)

/** Xóa token khi đăng xuất. */
export const clearToken = (): void => localStorage.removeItem('svpro_token')

/** Kiểm tra đã đăng nhập chưa. */
export const isLoggedIn = (): boolean => !!getToken()

// ── Fetch wrapper ─────────────────────────────────────────────────────────────

/**
 * Hàm fetch nội bộ: tự thêm Authorization header và xử lý lỗi HTTP.
 * Trả về JSON đã parse hoặc ném Error với message từ server.
 */
async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {}),
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers })

  if (res.status === 401) {
    clearToken()
    window.dispatchEvent(new CustomEvent('svpro:auth-required'))
    throw new Error('Phiên đăng nhập hết hạn')
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }

  if (res.status === 204) return undefined as T
  return res.json()
}

// ── Auth API ──────────────────────────────────────────────────────────────────

export const authApi = {
  /** Đăng nhập, trả về token string. */
  login: async (username: string, password: string): Promise<string> => {
    const data = await apiFetch<{ access_token: string }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    setToken(data.access_token)
    return data.access_token
  },

  /** Lấy thông tin tài khoản hiện tại. */
  me: () => apiFetch<{ username: string; role: string }>('/api/auth/me'),
}

// ── Events API ────────────────────────────────────────────────────────────────

export interface AccessEvent {
  id: number
  event_type: string
  entity_type: string | null
  entity_id: string | null
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
  camera_id: string | null
  source_id: string | null
  reason: string | null
  event_timestamp: string
  alert_sent: boolean
}

export interface EventStats {
  date: string
  total: number
  by_severity: Record<string, number>
  top_cameras: Array<{ camera_id: string; count: number }>
}

export const eventsApi = {
  list: (params?: {
    camera_id?: string
    severity?: string
    event_type?: string
    limit?: number
    offset?: number
  }) => {
    const q = new URLSearchParams()
    if (params?.camera_id) q.set('camera_id', params.camera_id)
    if (params?.severity) q.set('severity', params.severity)
    if (params?.event_type) q.set('event_type', params.event_type)
    if (params?.limit) q.set('limit', String(params.limit))
    if (params?.offset) q.set('offset', String(params.offset))
    return apiFetch<AccessEvent[]>(`/api/events?${q}`)
  },

  stats: () => apiFetch<EventStats>('/api/events/stats'),
  get: (id: number) => apiFetch<AccessEvent>(`/api/events/${id}`),
}

// ── Cameras API ───────────────────────────────────────────────────────────────

export interface Camera {
  id: number
  name: string
  rtsp_url: string
  location: string | null
  zone: string | null
  ai_mode: string
  fps_limit: number
  enabled: boolean
  created_at: string
}

export const camerasApi = {
  list: () => apiFetch<Camera[]>('/api/cameras'),
  get: (id: number) => apiFetch<Camera>(`/api/cameras/${id}`),
  create: (body: Partial<Camera>) =>
    apiFetch<Camera>('/api/cameras', { method: 'POST', body: JSON.stringify(body) }),
  update: (id: number, body: Partial<Camera>) =>
    apiFetch<Camera>(`/api/cameras/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  delete: (id: number) =>
    apiFetch<void>(`/api/cameras/${id}`, { method: 'DELETE' }),
}

// ── Users API ─────────────────────────────────────────────────────────────────

export interface User {
  id: number
  person_id: string
  name: string
  role: string
  active: boolean
  access_zones: string[]
  has_embedding: boolean
  created_at: string
}

export const usersApi = {
  list: (params?: { role?: string; active?: boolean; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.role) q.set('role', params.role)
    if (params?.active !== undefined) q.set('active', String(params.active))
    if (params?.limit) q.set('limit', String(params.limit))
    return apiFetch<User[]>(`/api/users?${q}`)
  },
  get: (id: number) => apiFetch<User>(`/api/users/${id}`),
  create: (body: Partial<User> & { blacklist_reason?: string }) =>
    apiFetch<User>('/api/users', { method: 'POST', body: JSON.stringify(body) }),
  update: (id: number, body: Partial<User> & { blacklist_reason?: string }) =>
    apiFetch<User>(`/api/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deactivate: (id: number) =>
    apiFetch<void>(`/api/users/${id}`, { method: 'DELETE' }),
}

// ── Vehicles API ──────────────────────────────────────────────────────────────

export interface Vehicle {
  id: number
  plate_number: string
  plate_category: string | null
  is_blacklisted: boolean
  blacklist_reason: string | null
  registered_at: string
}

export const vehiclesApi = {
  list: (blacklistedOnly = false) =>
    apiFetch<Vehicle[]>(`/api/vehicles?blacklisted_only=${blacklistedOnly}`),
  create: (body: Partial<Vehicle>) =>
    apiFetch<Vehicle>('/api/vehicles', { method: 'POST', body: JSON.stringify(body) }),
  toggleBlacklist: (plate: string, blacklisted: boolean, reason?: string) => {
    const q = new URLSearchParams({ blacklisted: String(blacklisted) })
    if (reason) q.set('reason', reason)
    return apiFetch<{ plate_number: string; is_blacklisted: boolean }>(
      `/api/vehicles/${plate}/blacklist?${q}`, { method: 'PATCH' }
    )
  },
}

// ── Doors API ─────────────────────────────────────────────────────────────────

export interface Door {
  door_id: string
  name: string
  zone: string
  enabled: boolean
  relay_url: string
  open_ms: number
}

export const doorsApi = {
  list: () => apiFetch<Door[]>('/api/doors'),
  get: (id: string) => apiFetch<Door>(`/api/doors/${id}`),
  toggle: (id: string, enabled: boolean) =>
    apiFetch<{ message: string }>(`/api/doors/${id}/toggle?enabled=${enabled}`, { method: 'PATCH' }),
  // Hàm trigger mở cửa khẩn cấp từ dashboard vẫn cần quyền API Core hoặc admin.
  // Nếu backend API đòi api key, thao tác này có thể lỗi, nhưng ta cứ cung cấp endpoint test:
  trigger: (id: string, person_id = 'admin-manual', role = 'admin') =>
    apiFetch<{ granted: boolean; reason: string; timestamp: string }>(`/api/doors/${id}/trigger`, {
      method: 'POST',
      body: JSON.stringify({
        person_id: person_id,
        person_name: 'Admin Dashboard',
        person_role: role,
        camera_id: 'dashboard',
        source_id: 'manual',
        liveness_ok: true,
        zone_allowed: true,
        fr_confidence: 1.0
      })
    })
}

// ── Stream API (go2rtc) ────────────────────────────────────────────────────────

export interface StreamUrlSet {
  webrtc:    string
  hls:       string
  mse:       string
  rtsp:      string
  player_ui: string
}

export interface StreamInfo {
  camera_id: number
  source_id:  string
  urls:       StreamUrlSet
}

export interface StreamStatus {
  camera_id:  number
  source_id:  string
  active:     boolean
  producers:  number
  consumers:  number
  urls?:      StreamUrlSet
}

export const streamApi = {
  /** Lấy URLs stream cho 1 camera từ go2rtc. */
  getInfo: (camId: number) =>
    apiFetch<StreamInfo>(`/api/stream/${camId}/info`),

  /** Lấy trạng thái stream (active/consumers) từ go2rtc. */
  getStatus: (camId: number) =>
    apiFetch<StreamStatus>(`/api/stream/${camId}/status`),

  /** Danh sách tất cả streams đang active trên go2rtc. */
  listActive: () =>
    apiFetch<{ total: number; streams: Array<StreamInfo & { producers: number; consumers: number }> }>('/api/stream/active'),
}

// ── Images API ─────────────────────────────────────────────────────────────────

export interface SnapshotImage {
  id:             number
  camera_id:      string
  event_id:       string | null
  entity_id:      string | null
  entity_type:    string | null
  image_path:     string
  thumbnail_path: string | null
  storage_type:   string
  width:          number | null
  height:         number | null
  file_size_bytes: number | null
  detected_at:    string
  created_at:     string
}

export const imagesApi = {
  list: (params?: {
    camera_id?: string
    entity_id?: string
    entity_type?: string
    from?: string
    to?: string
    limit?: number
    offset?: number
  }) => {
    const q = new URLSearchParams()
    if (params?.camera_id)    q.set('camera_id', params.camera_id)
    if (params?.entity_id)    q.set('entity_id', params.entity_id)
    if (params?.entity_type)  q.set('entity_type', params.entity_type)
    if (params?.from)         q.set('from', params.from)
    if (params?.to)           q.set('to', params.to)
    if (params?.limit)        q.set('limit', String(params.limit))
    if (params?.offset)       q.set('offset', String(params.offset))
    return apiFetch<SnapshotImage[]>(`/api/images?${q}`)
  },
  get: (id: number) => apiFetch<SnapshotImage>(`/api/images/${id}`),
}
