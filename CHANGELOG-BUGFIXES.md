# SV-PRO Bug Fixes Changelog

> Danh sách tất cả bugs đã được phát hiện và fix trong lần audit này.

---

## [BUGFIX-001] — strangers.py: Table `strangers` không tồn tại
- **File**: `backend/routers/strangers.py`
- **Severity**: CRITICAL
- **Dòng**: 72, 88, 102, 111-115
- **Mô tả**: API truy vấn bảng `strangers` nhưng database schema định nghĩa bảng là `guest_faces`. Tất cả endpoint của strangers router sẽ trả lỗi 500 khi chạy.
- **Fix**: Đổi tất cả `FROM strangers` → `FROM guest_faces` và `DELETE FROM strangers` → `DELETE FROM guest_faces`.
- **Trạng thái**: FIXED

---

## [BUGFIX-002] — strangers.py: Column `id`, `camera_id`, `frame_count`, `face_crop_path` không tồn tại
- **File**: `backend/routers/strangers.py`
- **Severity**: CRITICAL
- **Dòng**: 67-76, 83-93, 111-115
- **Mô tả**: Schema `guest_faces` không có các cột `id` (SERIAL), `camera_id`, `frame_count`, `face_crop_path`. Model `StrangerOut` cũng định nghĩa các field không tồn tại.
- **Fix**:
  - Xóa column `id` khỏi SELECT và `StrangerOut`
  - Đổi `camera_id` → `source_id`, `frame_count` → `quality_frames` trong query
  - Xóa `face_crop_path` khỏi SELECT và model (thông tin nằm trong `metadata_json`)
  - Dùng `COALESCE(quality_frames, 0)` để xử lý NULL
- **Trạng thái**: FIXED

---

## [BUGFIX-003] — events.py: SQL Injection qua f-string interpolation
- **File**: `backend/routers/events.py`
- **Severity**: CRITICAL
- **Dòng**: 75-76
- **Mô tả**: Giá trị `from_dt` và `to_dt` được nối trực tiếp vào SQL string qua `${len(params)+1}` thay vì dùng parameterized query. Attacker có thể inject SQL.
- **Fix**: Dùng hàm `add()` helper giống các điều kiện khác:
  ```python
  if from_dt: add("event_timestamp", from_dt); conditions[-1] += " >="
  if to_dt:   add("event_timestamp", to_dt);   conditions[-1] += " <="
  ```
- **Trạng thái**: FIXED

---

## [BUGFIX-004] — events.py: LIMIT/OFFSET placeholder ngược
- **File**: `backend/routers/events.py`
- **Severity**: HIGH
- **Dòng**: 75-76, 85-86
- **Mô tả**: `$3` được dùng cho LIMIT nhưng params = [filters..., limit, offset]. Khi có 1 filter: LIMIT=$2 (offset), OFFSET=$3 (limit) → ngược nhau.
- **Fix**: Đổi `LIMIT $2 OFFSET $3` cố định thay vì tính động.
- **Trạng thái**: FIXED

---

## [BUGFIX-005] — users.py: LIMIT/OFFSET placeholder ngược
- **File**: `backend/routers/users.py`
- **Severity**: CRITICAL
- **Dòng**: 75
- **Mô tả**: Tương tự BUGFIX-004. Khi có role filter, `LIMIT $3 OFFSET $4` nhưng params=[active, role, limit, offset] → LIMIT=$2 (offset), OFFSET=$3 (limit).
- **Fix**: Đổi `LIMIT $2 OFFSET $3` cố định.
- **Trạng thái**: FIXED

---

## [BUGFIX-006] — vehicles.py: PATCH params parse sai kiểu
- **File**: `backend/routers/vehicles.py`
- **Severity**: CRITICAL
- **Dòng**: 57-63
- **Mô tả**: `blacklisted: bool` và `reason: Optional[str]` được khai báo không có `Body()`. FastAPI parse chúng là query parameters thay vì JSON body. Request body `{"blacklisted": true}` sẽ không được parse đúng.
- **Fix**: Thêm `from fastapi import Body` và đổi signature:
  ```python
  blacklisted: bool = Body(...),
  reason: Optional[str] = Body(None),
  ```
- **Trạng thái**: FIXED

---

## [BUGFIX-007] — cameras/users/vehicles: `db.execute()` so sánh với string
- **Files**:
  - `backend/routers/cameras.py` dòng 117-119
  - `backend/routers/users.py` dòng 140-142
  - `backend/routers/vehicles.py` dòng 69-70
- **Severity**: HIGH
- **Mô tả**: `db.execute()` trong asyncpg trả về `int` (số rows affected), không phải string `"DELETE 0"`. So sánh `result == "DELETE 0"` luôn False → luôn raise 404.
- **Fix**: Đổi thành `result == 0`.
- **Trạng thái**: FIXED

---

## [BUGFIX-008] — Dockerfile.backend: `requirements.txt` không tồn tại
- **File**: `Dockerfile.backend`
- **Severity**: CRITICAL
- **Dòng**: 14
- **Mô tả**: `COPY requirements.txt .` nhưng file `requirements.txt` không tồn tại trong repo (chỉ có `requirements-dev.txt`). Docker build sẽ fail.
- **Fix**: Tạo file `requirements.txt` với đầy đủ production dependencies.
- **Trạng thái**: FIXED

---

## [BUGFIX-009] — Dockerfile.backend: `config/` directory không tồn tại
- **File**: `Dockerfile.backend`
- **Severity**: CRITICAL
- **Dòng**: 32
- **Mô tả**: `COPY config/ ./config/` nhưng thư mục `config/` không tồn tại. Docker build sẽ fail.
- **Fix**: Xóa dòng `COPY config/ ./config/`.
- **Trạng thái**: FIXED

---

## [BUGFIX-010] — Dockerfile.backend: Healthcheck dùng `curl` không có trong image
- **File**: `Dockerfile.backend`
- **Severity**: HIGH
- **Dòng**: 39
- **Mô tả**: Healthcheck dùng `curl` nhưng base image `python:3.11-slim` không có `curl`.
- **Fix**: Thay bằng Python healthcheck: `python -c "import urllib.request; urllib.request.urlopen(...)"`.
- **Trạng thái**: FIXED

---

## [BUGFIX-011] — module.yml: Hardcoded database credentials
- **File**: `module/module.yml`
- **Severity**: CRITICAL
- **Dòng**: 135
- **Mô tả**: Password `svpro_pass` hardcoded trong YAML config. Nên đọc từ biến môi trường.
- **Fix**: Đổi thành `${POSTGRES_DSN:-...}` và thêm env var trong docker-compose.yml cho `savant-ai-core`.
- **Trạng thái**: FIXED

---

## [BUGFIX-012] — auth.py: Hardcoded JWT secret và default password
- **File**: `backend/routers/auth.py`
- **Severity**: SERIOUS
- **Dòng**: 21, 103
- **Mô tả**:
  - `JWT_SECRET` có default `"change-me-in-production-please"` → attacker có thể decode tokens
  - `ADMIN_PASSWORD` có default `"svpro2024"` → brute-force trivial
- **Fix**:
  - `JWT_SECRET`: Bắt buộc set, raise RuntimeError nếu không có
  - `ADMIN_PASSWORD`: Bỏ default, bắt buộc set trong `.env`
- **Trạng thái**: FIXED

---

## [BUGFIX-013] — auth.py: Overly broad exception catch trong `_verify_token`
- **File**: `backend/routers/auth.py`
- **Severity**: MEDIUM
- **Dòng**: 61-68
- **Mô tả**: `except Exception` che giấu cả lỗi logic lẫn lỗi hệ thống (ImportError, etc.)
- **Fix**: Catch cụ thể `from jwt.exceptions import InvalidTokenError`
- **Trạng thái**: FIXED

---

## [BUGFIX-014] — doors.py: Deprecated `get_event_loop()` trong async context
- **File**: `backend/routers/doors.py`
- **Severity**: SERIOUS
- **Dòng**: 249, 252, 258
- **Mô tả**: `asyncio.get_event_loop().time()` deprecated từ Python 3.10, bị remove ở Python 3.12+.
- **Fix**: Dùng `asyncio.get_running_loop().time()` (luôn gọi trong async context).
- **Trạng thái**: FIXED

---

## [BUGFIX-015] — doors.py: Unused imports rải rác
- **File**: `backend/routers/doors.py`
- **Severity**: LOW
- **Dòng**: 16, 90, 244, 275, 297
- **Mô tả**: `time` ở module level nhưng không dùng (gọi `asyncio.get_running_loop().time()`). `json` và `logging` import trong function thay vì module level.
- **Fix**: Đưa `asyncio`, `json`, `aiohttp`, `logging` lên module level, xóa `time`.
- **Trạng thái**: FIXED

---

## [BUGFIX-016] — main.py: CORS credentials=True không validate origins
- **File**: `backend/main.py`
- **Severity**: SERIOUS
- **Dòng**: 34, 60-66
- **Mô tả**: `allow_credentials=True` với `allow_origins=["*"]` bị browser từ chối. Code hiện tại dùng env splitting không có wildcard nhưng không validate.
- **Fix**: Thêm validation không cho phép `"*"` trong origins khi credentials=True.
- **Trạng thái**: FIXED

---

## [BUGFIX-017] — users.py: Unused `import json`
- **File**: `backend/routers/users.py`
- **Severity**: LOW
- **Dòng**: 13
- **Mô tả**: Module import `json` nhưng không sử dụng ở bất kỳ đâu trong file.
- **Fix**: Xóa `import json`.
- **Trạng thái**: FIXED

---

## [BUGFIX-018] — FR: Stranger không được register vào Re-ID registry
- **File**: `src/fr/face_recognizer.py`
- **Severity**: HIGH
- **Dòng**: 457-459
- **Mô tả**: `_handle_stranger()` tạo stranger ID và lưu L1 cache nhưng KHÔNG bao giờ gọi `stranger_registry.register()`. Module `stranger_reid.py` tồn tại nhưng không được sử dụng → multi-camera Re-ID không hoạt động.
- **Fix**: Gọi `stranger_registry.register(stranger_id, state.centroid, source_id)` sau khi tạo stranger mới.
- **Trạng thái**: FIXED

---

## [BUGFIX-019] — stranger_reid.py: Centroid mới chưa L2-normalize
- **File**: `src/fr/stranger_reid.py`
- **Severity**: HIGH
- **Dòng**: 101-110
- **Mô tả**: Khi tạo `StrangerEntry` mới, `centroid = embedding.copy()` không được normalize L2. Các entry được update sau đó có normalize (dòng 91-95) nhưng entry đầu tiên thì không.
- **Fix**: Thêm normalize ngay khi tạo:
  ```python
  centroid = embedding.copy()
  norm = np.linalg.norm(centroid)
  if norm > 1e-6: centroid /= norm
  ```
- **Trạng thái**: FIXED

---

## [BUGFIX-020] — face_quality.py: Sharpness scoring không nhất quán với threshold
- **File**: `src/fr/face_quality.py`
- **Severity**: HIGH
- **Dòng**: 108
- **Mô tả**: `sharp_score = min(sharpness / 500.0, 1.0)` nhưng `_MIN_SHARPNESS = 50.0`. Ảnh sharpness=100 (gấp 2x ngưỡng) chỉ có score=0.2, quá thấp.
- **Fix**: Đổi thành `min(sharpness / 100.0, 1.0)` để sharpness=100 → score=1.0.
- **Trạng thái**: FIXED

---

## [BUGFIX-021] — FR: NMS IoU có thể gây NaN
- **File**: `src/fr/face_recognizer.py`
- **Severity**: HIGH
- **Dòng**: 597-600
- **Mô tả**: `np.maximum(1, areas[i] + areas[order[1:]])` không đúng cách tránh division by zero. `np.maximum(1, ...)` không đủ an toàn cho numpy arrays.
- **Fix**: Dùng `np.maximum(1e-6, ...)` thay thế.
- **Trạng thái**: FIXED

---

## [BUGFIX-022] — FR: Redis keys() trả về bytes không xử lý
- **Files**:
  - `src/fr/face_recognizer.py` dòng 726-743
  - `src/fr/stranger_reid.py` dòng 239-244
- **Severity**: LOW
- **Mô tả**: `redis.keys()` trả về list of bytes tùy Redis client version. Khi dùng làm key cho `get()` có thể gây mismatch.
- **Fix**: Decode bytes key trước khi dùng: `key.decode() if isinstance(key, bytes) else key`
- **Trạng thái**: FIXED

---

## [BUGFIX-023] — FR: `_disabled` không khởi tạo trong `__init__`
- **File**: `src/fr/face_recognizer.py`
- **Severity**: MEDIUM
- **Dòng**: 194-204
- **Mô tả**: `self._disabled` được gán trong `on_start()` (dòng 214) nhưng được đọc trong `process_frame()` mà không check tồn tại (dùng `getattr` an toàn). Nên khởi tạo rõ ràng trong `__init__`.
- **Fix**: Thêm `self._disabled = False` vào cuối `__init__`.
- **Trạng thái**: FIXED

---

## [BUGFIX-024] — blacklist_engine.py: Redis pipeline không thực thi lệnh nào
- **File**: `src/business/blacklist_engine.py`
- **Severity**: CRITICAL
- **Dòng**: 167, 171, 178, 181
- **Mô tả**: `pipe = self._redis.pipeline()` được tạo ở dòng 167 nhưng tất cả `setex` gọi trực tiếp trên `self._redis` thay vì `pipe.setex()`. `pipe.execute()` ở dòng 181 không làm gì cả → blacklist không được batch load lên Redis.
- **Fix**: Đổi `self._redis.setex()` → `pipe.setex()` trong cả 2 vòng lặp.
- **Trạng thái**: FIXED

---

## [BUGFIX-025] — blacklist_engine.py: Counter `count` không đầy đủ
- **File**: `src/business/blacklist_engine.py`
- **Severity**: LOW
- **Dòng**: 172, 179, 184
- **Mô tả**: `count` chỉ tăng trong vòng lặp blacklist người (dòng 172), không tăng trong vòng lặp blacklist xe → `logger.info` hiển thị số thiếu một nửa.
- **Fix**: Thêm `count += 1` trong vehicle loop.
- **Trạng thái**: FIXED

---

## [BUGFIX-026] — LPR: Duplicate exception handling trong plate detection
- **File**: `src/lpr/plate_ocr.py`
- **Severity**: MEDIUM
- **Dòng**: 1007-1012
- **Mô tả**: Khi exception xảy ra trong `_detect_plates`, code gọi lại `_detect_plates` lần 2 (không có metrics timing). Gây double processing.
- **Fix**: Chỉ catch exception và set `plates = []`, thêm log warning.
- **Trạng thái**: FIXED

---

## [BUGFIX-027] — ingress_manager.py: `SIGTERM` không tồn tại trên Windows
- **File**: `src/ingress/ingress_manager.py`
- **Severity**: HIGH
- **Dòng**: 67
- **Mô tả**: `signal.SIGTERM` không tồn tại trên Windows (chỉ có `SIGINT`). Đăng ký handler cho SIGTERM trên Windows gây `AttributeError`.
- **Fix**: Wrap trong `if sys.platform != "win32"`.
- **Trạng thái**: FIXED

---

## [BUGFIX-028] — ingress_manager.py: `fcntl` không tồn tại trên Windows
- **File**: `src/ingress/ingress_manager.py`
- **Severity**: HIGH
- **Dòng**: 234-244
- **Mô tả**: `import fcntl` ở trong function nhưng `fcntl` là Unix-only. Trên Windows, `proc.stderr.fileno()` vẫn hoạt động nhưng `fcntl.fcntl()` sẽ raise `AttributeError`.
- **Fix**: Wrap toàn bộ `fcntl` setup trong `if sys.platform != "win32"`.
- **Trạng thái**: FIXED

---

## [BUGFIX-029] — ingress_manager.py: Unix stderr read không đúng line boundary
- **File**: `src/ingress/ingress_manager.py`
- **Severity**: MEDIUM
- **Dòng**: 265
- **Mô tả**: `os.read(fd, 4096)` đọc raw bytes cố định, không theo line boundaries. Log message dài > 4096 chars bị cắt giữa dòng → EOS pattern matching có thể miss.
- **Fix**: Loop đọc cho đến khi hết dữ liệu, decode toàn bộ buffer.
- **Trạng thái**: FIXED

---

## [BUGFIX-030] — docker-compose.yml: `savant-ai-core` không phụ thuộc `postgres`
- **File**: `docker-compose.yml`
- **Severity**: HIGH
- **Dòng**: 34-36
- **Mô tả**: `savant-ai-core` chỉ phụ thuộc `db-init` (chạy 1 lần), không phụ thuộc `postgres` service. Race condition: ai-core có thể khởi động trước khi postgres ready.
- **Fix**: Thêm `depends_on postgres: condition: service_healthy` cho `savant-ai-core`.
- **Trạng thái**: FIXED

---

## [BUGFIX-031] — docker-compose.yml: `command` path thiếu leading slash
- **File**: `docker-compose.yml`
- **Severity**: HIGH
- **Dòng**: 53
- **Mô tả**: `command: user_data/module/module.yml` thiếu leading slash. Trong Docker, working directory và paths khác nhau.
- **Fix**: Sửa thành `/opt/savant/user_data/module/module.yml`.
- **Trạng thái**: FIXED

---

## [BUGFIX-032] — docker-compose.yml: Grafana depends_on không có condition
- **File**: `docker-compose.yml`
- **Severity**: MEDIUM
- **Dòng**: 226-227
- **Mô tả**: `depends_on: - prometheus` (simple list) không đảm bảo prometheus healthy trước khi grafana start.
- **Fix**: Đổi thành `depends_on: prometheus: condition: service_healthy`.
- **Trạng thái**: FIXED

---

## Tổng kết

| Bug ID | Severity | File | Status |
|--------|----------|------|--------|
| BUGFIX-001 | CRITICAL | strangers.py | FIXED |
| BUGFIX-002 | CRITICAL | strangers.py | FIXED |
| BUGFIX-003 | CRITICAL | events.py | FIXED |
| BUGFIX-005 | CRITICAL | users.py | FIXED |
| BUGFIX-006 | CRITICAL | vehicles.py | FIXED |
| BUGFIX-008 | CRITICAL | Dockerfile.backend | FIXED |
| BUGFIX-009 | CRITICAL | Dockerfile.backend | FIXED |
| BUGFIX-011 | CRITICAL | module.yml | FIXED |
| BUGFIX-024 | CRITICAL | blacklist_engine.py | FIXED |
| BUGFIX-004 | HIGH | events.py | FIXED |
| BUGFIX-007 | HIGH | cameras/users/vehicles | FIXED |
| BUGFIX-010 | HIGH | Dockerfile.backend | FIXED |
| BUGFIX-018 | HIGH | face_recognizer.py | FIXED |
| BUGFIX-019 | HIGH | stranger_reid.py | FIXED |
| BUGFIX-020 | HIGH | face_quality.py | FIXED |
| BUGFIX-021 | HIGH | face_recognizer.py | FIXED |
| BUGFIX-027 | HIGH | ingress_manager.py | FIXED |
| BUGFIX-028 | HIGH | ingress_manager.py | FIXED |
| BUGFIX-030 | HIGH | docker-compose.yml | FIXED |
| BUGFIX-031 | HIGH | docker-compose.yml | FIXED |
| BUGFIX-012 | SERIOUS | auth.py | FIXED |
| BUGFIX-014 | SERIOUS | doors.py | FIXED |
| BUGFIX-016 | SERIOUS | main.py | FIXED |
| BUGFIX-013 | MEDIUM | auth.py | FIXED |
| BUGFIX-023 | MEDIUM | face_recognizer.py | FIXED |
| BUGFIX-026 | MEDIUM | plate_ocr.py | FIXED |
| BUGFIX-029 | MEDIUM | ingress_manager.py | FIXED |
| BUGFIX-032 | MEDIUM | docker-compose.yml | FIXED |
| BUGFIX-015 | LOW | doors.py | FIXED |
| BUGFIX-017 | LOW | users.py | FIXED |
| BUGFIX-022 | LOW | face_recognizer.py / stranger_reid.py | FIXED |
| BUGFIX-025 | LOW | blacklist_engine.py | FIXED |

**Tổng cộng: 32 bugs đã được fix**
- 10 CRITICAL
- 11 HIGH
- 7 MEDIUM
- 4 LOW
