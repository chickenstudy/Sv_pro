# Kế hoạch Phát triển SV-PRO (Project Roadmap)

Lịch trình phát triển sản phẩm chia làm **5 Sprint** (mỗi Sprint 1–2 tuần). Mỗi Sprint bao gồm các task gốc và các task cải tiến mới được đề xuất sau phân tích kỹ thuật sâu.

> **Quy ước trạng thái:** `[x]` = Done · `[-]` = In Progress · `[ ]` = Todo · `[!]` = Blocking/Critical

---

## Tổng quan tiến độ

| Sprint | Tên | Tuần | Trạng thái |
|--------|-----|------|------------|
| Sprint 1 | Hạ tầng & Thiết kế | Tuần 1–2 | 🔵 In Progress |
| Sprint 2 | LPR & Phương tiện | Tuần 3–4 | ⚪ Todo |
| Sprint 3 | Face Recognition | Tuần 5–7 | ⚪ Todo |
| Sprint 4 | Logic Nghiệp vụ & Cảnh báo | Tuần 8–9 | ⚪ Todo |
| Sprint 5 | Dashboard & Đóng gói | Tuần 10–11 | ⚪ Todo |

---

## Sprint 1: Hạ tầng & Thiết kế rập khuôn (The Foundation)

> **Mục tiêu:** Có môi trường chạy được pipeline cơ bản end-to-end (Ingress → AI Core → JSON Egress) trước khi tích hợp model AI.

### Tasks thiết kế & tài liệu

- [x] **Task 1.1** — Phân tích kiến trúc `vms-savant`, quy hoạch thiết kế Dual-AI Pipeline (LPR + FR).
- [x] **Task 1.2** — Viết tài liệu kỹ thuật đầy đủ: `README.md`, `Architecture.md`, `Face_Recognition.md`, `DATABASE.md`, `Project_Plan.md`.

### Tasks hạ tầng

- [!] **Task 1.3** — **[CRITICAL]** Bật **NVIDIA Persistence Mode** trên host trước tất cả các bước còn lại.
  ```bash
  sudo systemctl enable --now nvidia-persistenced
  nvidia-smi --query-gpu=persistence_mode --format=csv,noheader  # → Enabled
  ```

- [ ] **Task 1.4** — Cài đặt Docker Stack gốc:
  - NVIDIA Savant base image + DeepStream 7.x
  - PostgreSQL 16 + extension `pgvector` ≥ 0.7
  - Redis 7.x
  - Prometheus + Grafana (monitoring baseline)

- [ ] **Task 1.5** — Thiết lập ZeroMQ IPC sockets:
  - Tạo thư mục `/tmp/sv-ipc/` với quyền phù hợp
  - Cấu hình Ingress REQ → `input-video.ipc`
  - Cấu hình AI Core REP/PUB
  - Cấu hình JSON Egress SUB

- [ ] **Task 1.6** — **[CẢI TIẾN]** Cấu hình **ZMQ High-Water Mark (HWM)**:
  - `sndwm = 100`, `rcvhwm = 100`, `sndtimeo = 200ms`
  - Chính sách DROP thay vì block vô hạn khi buffer đầy
  - *Tại sao cần ngay Sprint 1:* Nếu bỏ qua, OOM crash có thể xảy ra trong khi test và khó debug.

- [ ] **Task 1.7** — **[CẢI TIẾN]** Implement **EOS Storm Guard** trong `savant-video-ingress`:
  - Throttle EOS events: > 5 EOS/giây → flush queue + reconnect
  - *Tại sao cần ngay Sprint 1:* Camera test thường bị ngắt kết nối nhiều lần.

- [ ] **Task 1.8** — **[CẢI TIẾN]** Setup **Health-check Watchdog**:
  - Docker `healthcheck` cho từng service
  - `restart: unless-stopped` trong `docker-compose.yml`
  - Alert Telegram khi service restart > 3 lần trong 10 phút

- [ ] **Task 1.9** — **[CẢI TIẾN]** Instrument & đo `queue depth` / backlog:
  - Thêm metrics Prometheus cho REQ/REP và PUB/SUB (theo camera/source_id)
  - Ghi log định kỳ: queue depth, send/ack latency, number of dropped frames
  - Chuẩn hóa `trace_id` từ ingress → AI core → egress → log entry để truy vết

- [ ] **Task 1.10** — **[CẢI TIẾN]** Implement Drop policy theo HWM/timeout (code-level):
  - REQ/REP: timeout send + policy DROP frame khi buffer đầy (không block vô hạn)
  - PUB/SUB: drop frame cũ nhất (FIFO) khi subscriber bị lag
  - Thêm “drop reason codes” để hiểu đang drop do congestion hay EOS storm

- [ ] **Task 1.11** — **[CẢI TIẾN]** Runbook restart loop với circuit breaker:
  - Detect symptom: “JSON egress không tăng” / “AI core mất progress” / “FPS giảm đột ngột”
  - Restart theo thứ tự ưu tiên (egress → ai core → ingress) với backoff
  - Circuit breaker: giới hạn N lần restart / 10 phút, sau đó chuyển sang trạng thái `degraded` và yêu cầu operator can thiệp

- [ ] **Task 1.12** — **[CẢI TIẾN]** EOS storm guard chạy kèm flush queue + restart isolation:
  - EOS storm → flush queue + reconnect ingress
  - Không restart AI core nếu chỉ có sự cố ingress (tránh gián đoạn toàn pipeline)
  - Nếu phát hiện “no metadata output” kéo dài → mới restart AI core

- [ ] **Task 1.13** — Khởi tạo schema database:
  - Chạy migration: tạo tables, HNSW index, partitioned `recognition_logs`
  - Tạo partition tháng hiện tại và 2 tháng tiếp theo
  - Seed data test: 3 cameras, 10 users mẫu

- [ ] **Task 1.14** — Viết script `scripts/download_models.py`:
  - Download SCRFD-10GF, ArcFace R100, YOLOv8s/n, PaddleOCR v4, MiniFASNet
  - Verify checksum SHA256 sau download
  - Lưu tại `models/` dạng ONNX

### Definition of Done — Sprint 1

- [ ] `docker compose up` không có error
- [ ] `nvidia-smi` hiển thị `persistence_mode = Enabled`
- [ ] ZMQ Ingress → AI Core → JSON Egress pipeline chạy với RTSP camera test
- [ ] Grafana dashboard hiển thị FPS, GPU%, VRAM metrics
- [ ] pgvector HNSW index tạo thành công, query test trả về trong < 5ms

---

## Sprint 2: LPR & Phương tiện (Vehicle Domain)

> **Mục tiêu:** LPR pipeline đầy đủ hoạt động: từ RTSP frame → đọc biển số → lưu DB → xuất JSON.

### Tasks core LPR

- [ ] **Task 2.1** — Convert YOLOv8s và YOLOv8n sang **TensorRT FP16 engine**:
  ```bash
  python scripts/convert_trt.py --model models/yolov8s.onnx --fp16 --output models/engine/yolov8s_fp16.engine
  python scripts/convert_trt.py --model models/yolov8n_plate.onnx --fp16 --output models/engine/yolov8n_plate_fp16.engine
  ```
  *Lưu ý: Engine file phụ thuộc vào GPU cụ thể, không share giữa các máy.*

- [ ] **Task 2.2** — Tích hợp logic `PlateOCR` PyFunc (`src/lpr/plate_ocr.py`):
  - Crop vehicle ROI → YOLOv8n detect plate → CLAHE preprocessing → PaddleOCR
  - Xử lý biển 2 dòng (Split-line OCR Fallback)
  - Normalize format biển số Việt Nam
  - Unit test với bộ ảnh biển số mẫu (> 100 ảnh, đủ loại: car/moto/truck, ngày/đêm)

- [ ] **Task 2.3** — Porting hệ thống **ROI Eval & Heatmap Scheduler** từ `vms-savant`:
  - Cron job 23:50 hàng ngày
  - Heatmap 50×50px từ position log 3 ngày gần nhất
  - Export `/reports/roi_YYYYMMDD.json`

- [ ] **Task 2.3.1** — **[CẢI TIẾN]** Chuẩn hóa Data Contract LPR Event:
  - Thống nhất field set cho JSON egress (plate_number/plate_category/conf + bbox + files)
  - Tách trường `plate_raw` (nếu có) và trường `plate_number` (đã normalize)
  - Định nghĩa rõ record type: `plate` vs `NOT_DETECTED`

- [ ] **Task 2.3.2** — **[CẢI TIẾN]** Wiring `roi_zones` theo `source_id`:
  - Map khóa `roi_zones[source_id]` ↔ `frame_meta.source_id`
  - Validate khi không tồn tại key (fallback: process full frame hoặc skip theo policy)
  - Bắt buộc xử lý khác format key (ví dụ `cam_01` vs `cam_online_1`)

### Tasks cải tiến LPR

- [ ] **Task 2.4** — **[CẢI TIẾN]** Implement **Temporal Smoothing OCR**:
  - Vote majority trên 10 frame liên tiếp cho cùng Tracker ID
  - Buffer kết quả OCR theo `object_id` từ NvDCF Tracker
  - Chọn biển số xuất hiện nhiều nhất → giảm nhiễu ánh đèn nhấp nháy

- [ ] **Task 2.5** — **[CẢI TIẾN]** Implement **Night Mode preprocessing**:
  - Detect frame tối: `mean_brightness < 80`
  - Apply Histogram Equalization + tăng độ tương phản CLAHE
  - Benchmark accuracy ban đêm với test set ảnh IR camera

- [ ] **Task 2.6** — **[CẢI TIẾN]** Implement **LPR Confidence Ensemble**:
  - Chạy PaddleOCR 3 lần với rotation augment (-5°, 0°, +5°)
  - Vote majority kết quả 3 lần
  - Chỉ áp dụng khi confidence lần đầu < 0.75 (tránh overhead không cần thiết)

- [ ] **Task 2.7** — **[CẢI TIẾN]** Disk I/O off the hot path + Dedup đúng ngữ nghĩa:
  - Enqueue save task (background writer) để tránh block pyfunc hot path
  - Dedup theo `(source_id, plate_number)` với TTL riêng cho plate hợp lệ
  - Throttle `NOT_DETECTED` theo `(source_id, track_id)` để tránh flooding

- [ ] **Task 2.8** — **[CẢI TIẾN]** Warmup + failure mode cho ONNX/PaddleOCR:
  - Warm ONNX session (dummy run) trước khi stream thực
  - Warm PaddleOCR (first inference) để tránh “initial CUDA compilation spike”
  - Khi warmup/thất bại: giảm FPS/skip OCR (không crash pipeline)

- [ ] **Task 2.9** — **[CẢI TIẾN]** LPR Debug Overlay & Attribute Binding:
  - Implement `DebugDrawFunc` overlay bbox + plate number attribute `lpr/plate_number`
  - Ensure converter + pyfunc gán bbox/attr đúng không gian tọa độ mà drawfunc mong đợi
  - Bật debug log (option) cho: plate bbox degenerate, OCR empty, dedup skip

### Definition of Done — Sprint 2

- [ ] Đọc được > 90% biển số trong test set ban ngày (1000 ảnh)
- [ ] Đọc được > 75% biển số trong test set ban đêm (500 ảnh IR)
- [ ] Split-line OCR hoạt động đúng cho biển xe máy 2 dòng
- [ ] JSON output LPR event có đầy đủ fields theo schema
- [ ] Temporal Smoothing giảm lỗi đọc sai xuống < 5%

---

## Sprint 3: Face Recognition & Nhận diện người (Human Domain)

> **Mục tiêu:** FR pipeline đầy đủ: phát hiện mặt → embedding → match DB → Stranger tracking.

### Tasks core FR

- [ ] **Task 3.1** — Tích hợp **SCRFD-10GF + ArcFace R100** (ONNX Runtime):
  - SCRFD detect face + 5-point landmark
  - AffineTransform alignment 112×112px
  - ArcFace embedding 512-dim

- [ ] **Task 3.2** — Viết Plugin `FaceRecognizer` PyFunc (`src/fr/face_recognizer.py`):
  - gRPC client gửi embedding đến `sv-api-backend`
  - Backend query pgvector HNSW, trả về Person_ID
  - Xử lý timeout gRPC (< 50ms, fallback = skip frame)

- [ ] **Task 3.3** — Xây dựng **Stranger Tracking logic**:
  - SHA256[:8] ID generation
  - Insert vào `guest_faces` sau >= 3 quality frames
  - NvDCF Tracker binding để giữ ID ổn định

- [ ] **Task 3.4** — Tích hợp **Redis cache** 2 tầng:
  - Process-local LRU cache (capacity=1000, TTL=60s)
  - Redis shared cache với prefetch toàn bộ `role='staff'` khi startup

### Tasks cải tiến FR

- [ ] **Task 3.5** — **[CẢI TIẾN]** Implement **Face Quality Filter** đa chiều:
  - Blur score (Laplacian variance > 50)
  - Pose angle (yaw < 30°, pitch < 25°)
  - Illumination score (0.25–0.95)
  - Composite quality score weighted average > 0.50

- [ ] **Task 3.6** — **[CẢI TIẾN]** Tích hợp **Anti-Spoofing MiniFASNet**:
  - Download model: `models/anti_spoof/minifasnet_mn3.onnx`
  - Integrate vào pipeline trước bước ArcFace
  - Configurable per-camera (bật cho access control, tắt cho giám sát thông thường)
  - Log spoof attempts vào `access_events`

- [ ] **Task 3.7** — **[CẢI TIẾN]** Implement **Federated Stranger Re-ID**:
  - Background job mỗi 5 phút
  - Tìm stranger profile có cosine > 0.60 xuất hiện ở 2+ camera trong 10 phút
  - Merge thành 1 canonical profile

- [ ] **Task 3.8** — **[CẢI TIẾN]** Thêm **embedding_version** versioning:
  - Ghi `embedding_version` vào DB khi tạo user
  - Script `scripts/reembed_users.py` để re-embed khi đổi model
  - Query filter theo version để tránh cross-model matching

### Definition of Done — Sprint 3

- [ ] Nhận diện đúng > 95% khuôn mặt trong test set (100 người, 10 ảnh/người)
- [ ] False acceptance rate < 0.1% (ảnh người lạ không được nhận là người quen)
- [ ] Anti-spoofing block được ảnh in với độ chính xác > 98%
- [ ] Stranger ID ổn định qua > 100 frames liên tiếp
- [ ] Latency end-to-end FR < 100ms (GPU ArcFace + Redis cache hit)

---

## Sprint 4: Logic Nghiệp vụ & Cảnh báo (Smart Rules)

> **Mục tiêu:** Xây dựng lớp nghiệp vụ: blacklist, alerts, access control, Object Linking.

- [ ] **Task 4.1** — **Object Linking** — Liên kết biển số xe với khuôn mặt người đi cùng:
  - Spatial proximity: xe và người cách nhau < 150px trong cùng frame
  - Temporal window: 2 giây
  - Lưu liên kết vào `recognition_logs.metadata_json`

- [ ] **Task 4.2** — **Blacklist/Whitelist Engine**:
  - Kiểm tra `users.role = 'blacklist'` sau mỗi face match
  - Kiểm tra `vehicles.is_blacklisted` sau mỗi LPR match
  - Kiểm tra `users.access_zones` với `cameras.location_zone`
  - Rule builder: cấu hình per-camera, per-zone, per-time-range

- [ ] **Task 4.3** — **Alert System** — Webhook + Telegram Bot:
  - Trigger khi: blacklist detected / stranger in restricted zone / spoof attempt
  - Payload: timestamp, camera, image crop, event type
  - Rate limit: 1 alert/entity/5 phút (tránh spam)
  - Template cấu hình trong `config/alerts.yml`

- [ ] **Task 4.4** — **[CẢI TIẾN]** Access Control Integration:
  - HTTP endpoint: `POST /api/door/{door_id}/trigger` để mở cửa
  - Trigger khi: face match + liveness pass + zone allowed
  - Log vào bảng `access_events`
  - Support GPIO relay via Raspberry Pi hoặc HTTP relay controller

- [ ] **Task 4.5** — **[CẢI TIẾN]** Audit Log đầy đủ:
  - Lưu full JSON metadata + ảnh crop cho mọi event level ALERT
  - Retention: 90 ngày cho normal events, 1 năm cho alert events
  - Export endpoint: `GET /api/audit?from=&to=&camera=&type=`

### Definition of Done — Sprint 4

- [ ] Blacklist detection trigger alert trong < 2 giây
- [ ] Access control mở cửa trong < 500ms sau khi face match thành công
- [ ] Object Linking hoạt động với > 80% accuracy trong test scenario
- [ ] Alert Telegram gửi ảnh và thông tin đầy đủ

---

## Sprint 5: Dashboard & Đóng gói (UI & QA)

> **Mục tiêu:** Hệ thống có thể deploy cho khách hàng: UI hoàn chỉnh, stress test pass, 1-click install.

- [ ] **Task 5.1** — **React Dashboard** (Frontend):
  - Live stream viewer (WebRTC/HLS) với overlay BBox
  - Recognition log timeline (filter by camera, date, type)
  - User management: thêm/sửa/xóa user + face enrollment
  - Stranger gallery: xem và register stranger profiles
  - Camera management: RTSP config, ai_mode, fps_limit

- [ ] **Task 5.2** — **FastAPI Backend** hoàn chỉnh:
  - REST API: CRUD cameras, users, vehicles
  - gRPC server: nhận embedding từ AI Core, query pgvector
  - Auth: JWT (Dashboard) + API Key (AI Core internal)
  - Swagger/OpenAPI docs tại `/docs`

- [ ] **Task 5.3** — **Stress Test & Performance Profiling**:
  - 10 camera RTSP 1080p @ 15 FPS đồng thời
  - VRAM profiling: target < 10GB tổng (để có buffer cho model update)
  - Memory leak check sau 24h chạy liên tục
  - Recognition latency P50/P95/P99 dưới tải cao

- [ ] **Task 5.4** — **1-click Deployment Script**:
  - Interactive setup wizard: nhập RTSP URLs, DB password, Telegram token
  - Auto-check prerequisites (Docker, CUDA, NVIDIA persistence mode)
  - Auto-create DB schema và partitions
  - Auto-download models với progress bar

- [ ] **Task 5.5** — **[CẢI TIẾN]** OpenTelemetry Tracing tích hợp production:
  - Trace từ Ingress frame → AI Core → Egress
  - Export sang Jaeger/Grafana Tempo
  - Dashboard latency P95 per-camera, per-model

- [ ] **Task 5.6** — **[CẢI TIẾN]** Automated Backup:
  - Cron 2:00 AM hàng ngày: dump DB + copy ảnh crop quan trọng
  - Upload lên S3/MinIO
  - Retention: 7 ngày local, 30 ngày cloud
  - Alert khi backup thất bại

- [ ] **Task 5.7** — **[CẢI TIẾN]** Monthly Partition Auto-creation:
  - Cron đầu mỗi tháng: tạo partition tháng tiếp theo cho `recognition_logs` và `access_events`
  - Tự động DROP partition > 6 tháng (configurable retention)

- [ ] **Task 5.8** — **[CẢI TIẾN]** Control Plane Orchestration giống `vmsPro`:
  - Register camera/node (ingress group, ai-core group) + quản lý mapping `source_id`
  - Start/stop scale ingress adapter theo ai-core capacity
  - Async update cấu hình runtime (ngưỡng LPR, `roi_zones`, enable_lpr/enable_fr, fps_limit)
  - Health-check + restart có backoff (tránh restart loop)

### Definition of Done — Sprint 5

- [ ] 10 camera chạy ổn định 24h không restart
- [ ] VRAM < 10GB với 10 camera 1080p
- [ ] Latency FR P95 < 200ms, LPR P95 < 150ms dưới tải đầy
- [ ] Zero memory leak sau 24h (RSS process growth < 100MB)
- [ ] Setup từ đầu đến pipeline chạy được trong < 15 phút với 1-click script
- [ ] Backup tự động chạy thành công 3 ngày liên tiếp

---

## Tổng hợp Cải tiến Kỹ thuật theo Sprint

| Sprint | # | Cải tiến | Ưu tiên |
|--------|---|---------|---------|
| 1 | 1.6 | ZMQ High-Water Mark (HWM=100) | 🔴 Critical |
| 1 | 1.7 | EOS Storm Guard (throttle EOS flood) | 🔴 Critical |
| 1 | 1.8 | Health-check Watchdog + Auto-restart | 🟠 High |
| 1 | 1.9 | Instrument queue depth/backlog + trace_id logs | 🟠 High |
| 1 | 1.10 | Drop policy (REQ/REP timeout + PUB/SUB FIFO drop) | 🔴 Critical |
| 1 | 1.11 | Restart runbook with circuit breaker/backoff | 🟠 High |
| 1 | 1.12 | EOS flush queue + restart isolation (ingress vs ai-core) | 🟠 High |
| 2 | 2.4 | Temporal Smoothing OCR (10-frame vote) | 🟠 High |
| 2 | 2.5 | Night Mode preprocessing (CLAHE + HEQ) | 🟠 High |
| 2 | 2.6 | LPR Confidence Ensemble (3-angle OCR) | 🟡 Medium |
| 2 | 2.7 | Disk I/O off hot path + Dedup semantics | 🟠 High |
| 2 | 2.8 | Warmup + failure mode cho ONNX/PaddleOCR | 🔴 Critical |
| 2 | 2.9 | LPR Debug Overlay & Attribute Binding | 🟡 Medium |
| 3 | 3.5 | Face Quality Filter (blur + pose + illumination) | 🔴 Critical |
| 3 | 3.6 | Anti-Spoofing MiniFASNet (~3ms/face) | 🔴 Critical |
| 3 | 3.7 | Federated Stranger Re-ID (multi-camera merge) | 🟡 Medium |
| 3 | 3.8 | Embedding version versioning | 🟠 High |
| 4 | 4.4 | Access Control GPIO/HTTP relay | 🟡 Medium |
| 4 | 4.5 | Audit Log đầy đủ với retention policy | 🟠 High |
| 5 | 5.5 | OpenTelemetry Distributed Tracing | 🟡 Medium |
| 5 | 5.6 | Automated Backup (S3/MinIO) | 🟠 High |
| 5 | 5.8 | Control Plane Orchestration (vmsPro-like) | 🟠 High |

---

## Rủi ro kỹ thuật & Mitigation

| Rủi ro | Xác suất | Tác động | Mitigation |
|--------|----------|----------|------------|
| TensorRT engine không tương thích GPU khách hàng | Cao | Cao | Build engine on-device lần đầu; cache engine file per-GPU |
| pgvector HNSW OOM khi > 5M faces | Trung bình | Cao | Monitor VRAM; switch sang IVFFlat nếu cần |
| RTSP stream không ổn định gây EOS storm | Cao | Trung bình | EOS Storm Guard (Task 1.7) |
| ArcFace accuracy thấp với camera góc cao | Trung bình | Trung bình | ROI Autopilot loại vùng góc xấu; hướng dẫn lắp đặt camera |
| PaddleOCR đọc sai biển số loại mới | Thấp | Thấp | Fine-tune model với dataset biển số VN mới |

---

*© 2026 SV-PRO Project Management — Phiên bản 1.1*
