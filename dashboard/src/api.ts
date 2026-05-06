// ─ SV-PRO API Client ─────────────────────────────────────────────────────────
// Tất cả request đến FastAPI backend đều đi qua module này.
// Tự động đính kèm JWT token từ localStorage vào header Authorization.

const BASE_URL = import.meta.env.VITE_API_URL || 'http://192.168.42.171:8000'  // '' = relative path, Nginx proxy /api/* → backend:8000

// ── Dev offline bypass ────────────────────────────────────────────────────────
// Đăng nhập admin/admin sẽ dùng token này — không cần backend chạy.
const DEV_TOKEN = '__svpro_dev_admin_offline__'
const DEV_ME = { username: 'admin', role: 'admin' }

// ── Auth helpers ──────────────────────────────────────────────────────────────

/** Lấy JWT token đang lưu trong localStorage. */
export const getToken = (): string | null => localStorage.getItem('svpro_token')

/** Lưu JWT token sau khi đăng nhập thành công. */
export const setToken = (token: string): void => localStorage.setItem('svpro_token', token)

/** Xóa token khi đăng xuất. */
export const clearToken = (): void => localStorage.removeItem('svpro_token')

/** Kiểm tra đã đăng nhập chưa. */
export const isLoggedIn = (): boolean => !!getToken()

/** True khi đang dùng offline dev bypass — không có backend thật. */
export const isDevMode = (): boolean => getToken() === DEV_TOKEN

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
  /** Đăng nhập, trả về token string. admin/admin → offline dev bypass, không cần backend. */
  login: async (username: string, password: string): Promise<string> => {
    if (username === 'admin' && password === 'admin') {
      setToken(DEV_TOKEN)
      return DEV_TOKEN
    }
    const data = await apiFetch<{ access_token: string }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    setToken(data.access_token)
    return data.access_token
  },

  /** Lấy thông tin tài khoản hiện tại. */
  me: (): Promise<{ username: string; role: string }> => {
    if (getToken() === DEV_TOKEN) return Promise.resolve(DEV_ME)
    return apiFetch<{ username: string; role: string }>('/api/auth/me')
  },
}

// ── Events API ────────────────────────────────────────────────────────────────

export interface AccessEvent {
  // Backend trả ID dạng string (UNION giữa access_events.id INT và recognition_logs.event_id UUID)
  id: string
  event_type: string
  entity_type: string | null
  entity_id: string | null
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
  camera_id: string | null
  source_id: string | null
  reason: string | null
  event_timestamp: string
  alert_sent: boolean
  // Đường dẫn tương đối ảnh trong /Detect — FE build URL: /api/detect-images/{image_path}
  image_path: string | null
  /** Face cosine similarity 0..1 (chỉ recognition_logs face). */
  match_score?: number | null
  /** OCR confidence 0..1 (chỉ recognition_logs LPR). */
  ocr_confidence?: number | null
}

/**
 * URL ảnh detection — backend chấp nhận JWT qua query param ?t=
 * vì <img src> không thể gắn header Authorization.
 */
export function detectImageUrl(rel: string | null | undefined): string | null {
  if (!rel) return null
  const safe = rel.split('/').map(encodeURIComponent).join('/')
  const tok = getToken()
  return tok
    ? `/api/detect-images/${safe}?t=${encodeURIComponent(tok)}`
    : `/api/detect-images/${safe}`
}

export interface EventDetail {
  id: string
  source: 'recognition_log' | 'access_event'
  // ── recognition_log fields ─────────────────────────────────────────────────
  label?: string
  person_id?: string
  /** @deprecated Dùng fr_confidence. match_score giữ cho backward compat. */
  match_score?: number | null
  /** Face cosine similarity 0-1 (alias của match_score, canonical name). */
  fr_confidence?: number | null
  is_stranger?: boolean
  plate_number?: string | null
  plate_category?: string | null
  ocr_confidence?: number | null
  /** Tên hiển thị của người nhận diện (extract từ metadata_json). */
  person_name?: string | null
  /** Role: staff | admin | management | visitor | unknown (extract từ metadata_json). */
  person_role?: string | null
  /** Full raw metadata_json — chỉ cho debug/advanced use. */
  metadata?: Record<string, any>
  // ── access_event fields ────────────────────────────────────────────────────
  event_type?: string
  entity_type?: string | null
  entity_id?: string | null
  severity?: string
  reason?: string | null
  alert_sent?: boolean
  /** Top-level cho cả access_event (json_path) và recognition_log
   *  (metadata_json->>'image_path'). LPR: ảnh khung hình xe đầy đủ. */
  image_path?: string | null
  /** LPR: ảnh crop riêng của biển số (chỉ recognition_log). */
  plate_image_path?: string | null
  // ── common ─────────────────────────────────────────────────────────────────
  camera_id?: string | null
  source_id?: string | null
  event_timestamp: string
}

export interface EventStats {
  date: string
  total: number
  by_severity: Record<string, number>
  by_event_type: Record<string, number>
  top_cameras: Array<{ camera_id: string; count: number }>
}

export interface EventListResult {
  data: AccessEvent[]
  total: number   // từ X-Total-Count header
}

export interface EventListParams {
  camera_id?: string
  severity?: string
  event_type?: string
  from?: string
  to?: string
  limit?: number
  offset?: number
}

export const eventsApi = {
  /** URL cho SSE stream — token qua ?t= vì EventSource không gắn được header. */
  streamUrl: (): string => {
    const tok = getToken()
    return tok
      ? `${BASE_URL}/api/events/stream?t=${encodeURIComponent(tok)}`
      : `${BASE_URL}/api/events/stream`
  },

  list: (params?: EventListParams) => {
    const q = new URLSearchParams()
    if (params?.camera_id) q.set('camera_id', params.camera_id)
    if (params?.severity) q.set('severity', params.severity)
    if (params?.event_type) q.set('event_type', params.event_type)
    if (params?.from) q.set('from', params.from)
    if (params?.to) q.set('to', params.to)
    if (params?.limit) q.set('limit', String(params.limit))
    if (params?.offset) q.set('offset', String(params.offset))
    return apiFetch<AccessEvent[]>(`/api/events?${q}`)
  },

  /** Như list() nhưng trả về {data, total} — total từ X-Total-Count header. */
  listWithTotal: async (params?: EventListParams): Promise<EventListResult> => {
    const q = new URLSearchParams()
    if (params?.camera_id) q.set('camera_id', params.camera_id)
    if (params?.severity) q.set('severity', params.severity)
    if (params?.event_type) q.set('event_type', params.event_type)
    if (params?.from) q.set('from', params.from)
    if (params?.to) q.set('to', params.to)
    if (params?.limit) q.set('limit', String(params.limit))
    if (params?.offset) q.set('offset', String(params.offset))
    const token = getToken()
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers['Authorization'] = `Bearer ${token}`
    const res = await fetch(`${BASE_URL}/api/events?${q}`, { headers })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data: AccessEvent[] = await res.json()
    const total = parseInt(res.headers.get('X-Total-Count') ?? String(data.length), 10)
    return { data, total }
  },

  stats: () => apiFetch<EventStats>('/api/events/stats'),
  get: (id: string | number) => apiFetch<AccessEvent>(`/api/events/${id}`),
  getDetail: (id: string | number) => apiFetch<EventDetail>(`/api/events/${id}/detail`),
}

// ── LPR API (đọc trực tiếp JSON sidecar /Detect/lpr/) ─────────────────────────

export type LprCategory =
  | 'XE_MAY_DAN_SU' | 'O_TO_DAN_SU' | 'BIEN_CA_NHAN'
  | 'XE_MAY_DIEN'   | 'XE_QUAN_DOI'  | 'KHONG_XAC_DINH' | 'NOT_DETECTED'

export const LPR_CATEGORY_LABEL: Record<LprCategory, string> = {
  XE_MAY_DAN_SU:  'Xe máy dân sự',
  O_TO_DAN_SU:    'Ô tô dân sự',
  BIEN_CA_NHAN:   'Biển cá nhân',
  XE_MAY_DIEN:    'Xe máy điện',
  XE_QUAN_DOI:    'Xe quân đội',
  KHONG_XAC_DINH: 'Không xác định',
  NOT_DETECTED:   'Không đọc được',
}

export interface LprEvent {
  id:                string                   // rel path không có .json — FE key + lookup chi tiết
  json_path:         string
  source_id:         string
  camera_id:         string
  date:              string                   // YYYY-MM-DD
  category:          LprCategory | string
  label?:            string | null            // car / motorcycle / truck / bus
  plate_number?:     string | null
  plate_category?:   string | null
  ocr_confidence?:   number | null
  plate_det_confidence?: number | null
  timestamp?:        string | null
  image_path?:       string | null            // ảnh khung hình xe
  plate_image_path?: string | null            // crop biển số
}

export interface LprListResult {
  data:  LprEvent[]
  total: number
}

export interface LprStats {
  date:        string
  total:       number
  by_category: Record<string, number>
  by_camera:   Array<{ camera_id: string; count: number }>
}

export interface LprEventDetail extends LprEvent {
  raw: Record<string, any>   // raw JSON sidecar gồm bbox + files
}

export const lprApi = {
  list: async (params?: {
    date?: string; category?: string; camera?: string;
    search?: string; limit?: number; offset?: number;
  }): Promise<LprListResult> => {
    const q = new URLSearchParams()
    if (params?.date)     q.set('date', params.date)
    if (params?.category) q.set('category', params.category)
    if (params?.camera)   q.set('camera', params.camera)
    if (params?.search)   q.set('search', params.search)
    if (params?.limit)    q.set('limit', String(params.limit))
    if (params?.offset)   q.set('offset', String(params.offset))
    const tok = getToken()
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (tok) headers['Authorization'] = `Bearer ${tok}`
    const res = await fetch(`${BASE_URL}/api/lpr/events?${q}`, { headers })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data: LprEvent[] = await res.json()
    const total = parseInt(res.headers.get('X-Total-Count') ?? String(data.length), 10)
    return { data, total }
  },
  stats:    (date?: string) => apiFetch<LprStats>(`/api/lpr/stats${date ? `?date=${date}` : ''}`),
  cameras:  () => apiFetch<string[]>('/api/lpr/cameras'),
  /** id = rel_path không có .json (vd: lpr/bien_xe/2026-04-28/XE_MAY_DAN_SU/153919_434_motorcycle_29_E3_01246) */
  getDetail: (id: string) => apiFetch<LprEventDetail>(`/api/lpr/event/${id.split('/').map(encodeURIComponent).join('/')}`),
}

// ── Cameras API ───────────────────────────────────────────────────────────────

export interface RoiPoint { x: number; y: number }   // toạ độ chuẩn hoá [0,1]

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
  roi_polygon: RoiPoint[] | null
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
  /** URL ảnh snapshot 1 frame (kèm token) cho ROI editor. */
  snapshotUrl: (id: number) => {
    const tok = getToken()
    const cb  = `_=${Date.now()}`
    return tok
      ? `/api/stream/${id}/snapshot?t=${encodeURIComponent(tok)}&${cb}`
      : `/api/stream/${id}/snapshot?${cb}`
  },
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

// ── Face Enrollment API ───────────────────────────────────────────────────────

export interface EnrollResponse {
  success: boolean
  user_id: number
  name: string
  message: string
}

/**
 * Upload ảnh face để đăng ký cho user. Backend proxy sang AI Core
 * (SCRFD + ArcFace warmup sẵn) để extract embedding 512-dim, lưu vào
 * users.face_embedding (pgvector).
 */
async function enrollUploadFile(userId: number, file: File): Promise<EnrollResponse> {
  return enrollUploadMultipart(userId, [file], 'face')
}

async function enrollUploadFiles(userId: number, files: File[]): Promise<EnrollResponse & { failed?: { file: string; reason: string }[]; succeeded?: number }> {
  return enrollUploadMultipart(userId, files, 'faces')
}

async function enrollUploadMultipart(
  userId: number,
  files: File[],
  endpoint: 'face' | 'faces',
): Promise<any> {
  const token = getToken()
  const fd = new FormData()
  // /face dùng field 'file' (1 file), /faces dùng 'files' (list)
  if (endpoint === 'face') {
    fd.append('file', files[0])
  } else {
    for (const f of files) fd.append('files', f)
  }
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${BASE_URL}/api/enroll/${userId}/${endpoint}`, {
    method: 'POST',
    body: fd,
    headers,
  })
  if (res.status === 401) {
    clearToken()
    window.dispatchEvent(new CustomEvent('svpro:auth-required'))
    throw new Error('Phiên đăng nhập hết hạn')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const enrollApi = {
  uploadFace:  enrollUploadFile,
  uploadFaces: enrollUploadFiles,
  remove: (userId: number) =>
    apiFetch<void>(`/api/enroll/${userId}/face`, { method: 'DELETE' }),
  status: () => apiFetch<{
    ready?: boolean
    ai_core_status?: string
    available?: boolean
  }>(`/api/enroll/status`),
}

// ── Face Search API ───────────────────────────────────────────────────────────

export interface FaceMatch {
  type:        'user' | 'stranger'
  similarity:  number
  distance:    number
  // user fields
  user_id?:    number
  person_id?:  string
  name?:       string
  role?:       string
  // stranger fields
  stranger_id?: string
  last_image?:  string
  cameras?:     string[]
}

async function faceSearchByImage(
  file: File,
  opts: { limit?: number; min_similarity?: number; include_strangers?: boolean } = {},
): Promise<FaceMatch[]> {
  const token = getToken()
  const fd = new FormData()
  fd.append('file', file)
  const params = new URLSearchParams()
  if (opts.limit) params.set('limit', String(opts.limit))
  if (opts.min_similarity != null) params.set('min_similarity', String(opts.min_similarity))
  if (opts.include_strangers != null) params.set('include_strangers', String(opts.include_strangers))
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${BASE_URL}/api/face-search?${params}`, {
    method: 'POST', body: fd, headers,
  })
  if (res.status === 401) {
    clearToken()
    window.dispatchEvent(new CustomEvent('svpro:auth-required'))
    throw new Error('Phiên đăng nhập hết hạn')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const faceSearchApi = {
  search: faceSearchByImage,
}

// ── Settings API ──────────────────────────────────────────────────────────────

export interface AppSetting {
  key:        string
  value:      any
  updated_at: string
  updated_by: string | null
}

export interface RetentionRun {
  id:            number
  started_at:    string
  finished_at:   string | null
  triggered_by:  string
  deleted_files: number
  deleted_bytes: number
  deleted_rows:  Record<string, number> | null
  error:         string | null
}

export const settingsApi = {
  list: () => apiFetch<AppSetting[]>('/api/settings'),
  get:  (key: string) => apiFetch<AppSetting>(`/api/settings/${encodeURIComponent(key)}`),
  update: (key: string, value: any) =>
    apiFetch<AppSetting>(`/api/settings/${encodeURIComponent(key)}`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    }),
  runCleanup: () =>
    apiFetch<{
      run_id: number; files_deleted: number; bytes_deleted: number
      rows_deleted: Record<string, number>
      took_seconds?: number; error?: string | null
    }>(`/api/settings/cleanup/run`, { method: 'POST' }),
  listRuns: () => apiFetch<RetentionRun[]>('/api/settings/cleanup/runs'),
}

// ── Strangers API ─────────────────────────────────────────────────────────────

export interface StrangerImage {
  image_path: string
  source_id:  string | null
  created_at: string
  score:      number | null
}

export const strangersApi = {
  addNotes: (uid: string, notes: string) =>
    apiFetch<any>(`/api/strangers/${uid}/notes`, {
      method: 'POST',
      body: JSON.stringify({ notes }),
    }),
  remove: (uid: string) =>
    apiFetch<void>(`/api/strangers/${uid}`, { method: 'DELETE' }),
  listImages: (uid: string, limit = 60) =>
    apiFetch<StrangerImage[]>(`/api/strangers/${uid}/images?limit=${limit}`),
  dedup: (apply: boolean, threshold = 0.55) =>
    apiFetch<{
      clusters_found: number
      strangers_removed: number
      dry_run: boolean
      threshold: number
    }>(`/api/strangers/dedup?apply=${apply}&threshold=${threshold}`,
       { method: 'POST' }),
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
