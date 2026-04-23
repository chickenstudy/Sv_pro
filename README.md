# SV-PRO (Savant-Vision Professional)

> Hệ thống phân tích Video theo thời gian thực (Real-time Video Analytics) xây dựng trên nền tảng [Savant](https://github.com/insight-platform/Savant) + NVIDIA DeepStream 7.x.  
> `sv-pro` thực hiện đồng thời **2 luồng nhận diện (Dual-AI Pipeline)** trên các luồng camera RTSP: **Nhận diện Biển số xe (LPR)** và **Nhận diện Khuôn mặt (FR)** — xử lý song song, không cản trở lẫn nhau, trên cùng một GPU pipeline.

---

## Mục lục

- [Tính năng cốt lõi](#-tính-năng-cốt-lõi)
- [Kiến trúc tổng quan](#-kiến-trúc-tổng-quan)
- [Stack công nghệ](#-stack-công-nghệ)
- [Yêu cầu hệ thống](#%EF%B8%8F-yêu-cầu-hệ-thống)
- [Cài đặt và chạy hệ thống](#-cài-đặt-và-chạy-hệ-thống)
- [Cấu hình pipeline](#-cấu-hình-pipeline)
- [Output Metadata JSON](#-output-metadata-json)
- [Hệ thống đánh giá ROI Autopilot](#-hệ-thống-đánh-giá-roi-autopilot)
- [Giám sát & Monitoring](#-giám-sát--monitoring)
- [Known Issues & Troubleshooting](#%EF%B8%8F-known-issues--troubleshooting)
- [Tài liệu liên quan](#-tài-liệu-liên-quan)

---

## 🚀 Tính năng cốt lõi

### Dual-AI Pipeline (LPR + FR)
Hai luồng AI chạy song song trên cùng một DeepStream pipeline, chia sẻ chung tầng **Primary Object Detector (YOLOv8s TensorRT FP16)** để tối ưu VRAM, sau đó rẽ nhánh độc lập vào các `pyfunc` riêng biệt:

| Pipeline | Model Chain | Đầu ra |
|----------|-------------|--------|
| **LPR** | YOLOv8s → YOLOv8n → PaddleOCR v4 | Biển số chuẩn hóa, vehicle type, confidence |
| **FR** | YOLOv8s → SCRFD → ArcFace R100 → pgvector | Person ID / Stranger ID, match score, face quality |

### Kiến trúc phân tán ZMQ (IPC)
Các tiến trình **Ingress** (thu nhận stream), **AI Core** (xử lý model) và **Egress** (xuất dữ liệu) hoạt động độc lập qua ZeroMQ Unix socket. Cho phép restart từng thành phần mà không làm gián đoạn GPU pipeline.

### Nhận diện biển số chuyên dụng Việt Nam
- Đọc chuẩn xác biển xe máy **2 dòng** (Split-line OCR Fallback).
- Hỗ trợ biển quân đội, ngoại giao, xe điện.
- Temporal Smoothing: vote majority trên 10 frame liên tiếp để lọc nhiễu OCR.
- Night Mode: CLAHE + Histogram Equalization tự động khi độ sáng trung bình frame < 80.

### Match Face diện rộng qua pgvector
- Lưu trữ hàng chục triệu Face Embedding (512-dim Float32) với chỉ mục **HNSW** của pgvector.
- Redis hot-cache cho nhân viên thường trú: latency ~1ms/face.
- Fallback gRPC → Backend API → PostgreSQL khi cache miss: ~15ms/face.

### Stranger Tracking & Object Linking
- **Stranger Tracking**: Người chưa đăng ký được gán `STR_<SHA256[:8]>` ổn định qua nhiều frame nhờ DeepStream NvDCF Tracker. Vector lưu tạm bảng `guest_faces` TTL 7 ngày.
- **Object Linking** *(Sprint 4)*: Liên kết biển số xe với khuôn mặt người đi cùng dựa trên spatial-temporal proximity (cửa sổ 2 giây, bán kính 150px).

### Anti-Spoofing (Liveness Detection)
Tích hợp **MiniFASNet** (~3ms/face) phát hiện ảnh in hoặc màn hình giả mạo trước bước ArcFace Embedding — bắt buộc cho use case kiểm soát cửa.

### ROI Autopilot
Tiến trình `savant-roi-eval` chạy cron **23:50** hàng ngày: phân tích 3 ngày log → xây dựng Heatmap 50×50px → tự động tinh chỉnh `roi_zones` trong `module.yml`.

---

## 📐 Kiến trúc tổng quan

```
Camera RTSP (IP: 192.168.x.xxx)
       │  cam_gate_1 (15 FPS)       cam_parking_1 (10 FPS)
       │       │                           │
       ▼       ▼                           ▼
            ┌────────────────────────────────┐
            │   go2rtc (RTSP Broker)         │
            │   Port 1984: HTTP API + WebUI  │ ← go2rtc_sync.py
            │   Port 8554: RTSP re-stream    │   (PG NOTIFY → REST API)
            │   Port 8555: WebRTC (browser)  │
            └────────────────────────────────┘
                    │ rtsp://go2rtc:8554/{cam_id}
                    ▼
            ┌────────────────────────────────┐
            │  savant-rtsp-ingress           │
            │  cv2.VideoCapture per camera   │
            │  Frames → ZMQ PUB             │
            └────────────────────────────────┘
                    │ ZMQ IPC (input-video.ipc)
                    ▼
            ┌────────────────────────────────┐
            │   savant-ai-core (GPU)         │
            │   DeepStream 7.x Pipeline      │
            │                                │
            │   [Stage 1 — nvinfer]          │
            │   YOLOv8s TensorRT FP16        │
            │   Detect: Vehicle / Person     │
            │                                │
            │   [Stage 2a — PyFunc] PlateOCR │
            │   [Stage 2b — PyFunc] FaceRecog│
            │                                │
            │   [Stage 3 — PyFunc]           │
            │   BlacklistPyfunc              │
            └────────────────────────────────┘
                    │
           ┌────────┴────────┐
           ▼                 ▼
     [alert_manager]   [audit_logger]
     Telegram/Webhook  PostgreSQL (events)
```

> Chi tiết đầy đủ: [`docs/Architecture.md`](docs/Architecture.md)

---

## 🛠 Stack công nghệ

| Tầng | Công nghệ | Vai trò |
|------|-----------|---------|
| GPU Inference | NVIDIA DeepStream 7.x + TensorRT FP16 | Decode H.264/H.265 NVDEC + chạy model AI trên GPU |
| AI Framework | Savant (insight-platform) | Pipeline manager, ZMQ IPC, PyFunc plugin system |
| RTSP Broker | **go2rtc** | Nhận RTSP từ IP camera, re-stream cho Savant Ingress + WebRTC browser |
| Camera Sync | go2rtc_sync.py + PG NOTIFY | Đồng bộ camera DB → go2rtc REST API realtime |
| RTSP Ingress | rtsp_ingest.py (savant-rtsp-ingress) | Pull RTSP từ go2rtc → push ZMQ frames vào Savant AI Core |
| Person/Vehicle Det. | YOLOv8s (TensorRT) | Tầng 1: phát hiện người & xe trên full frame |
| Face Detection | SCRFD-10GF (ONNX Runtime) | 5-point landmark, face alignment trước ArcFace |
| Face Embedding | ArcFace R100 (InsightFace ONNX) | 512-dim Float32 vector, L2-normalized |
| Plate Detection | YOLOv8n (ONNX Runtime) | Dò bounding box biển số trong vehicle crop |
| OCR | PaddleOCR v4 (ONNX) | Đọc ký tự biển số, xử lý biển 2 dòng VN |
| Anti-Spoofing | MiniFASNet (ONNX) | Liveness detection ~3ms/face |
| Vector DB | PostgreSQL 16 + pgvector (HNSW) | Lưu & tìm kiếm cosine similarity hàng triệu face |
| Cache | Redis 7 | Hot-cache embedding nhân viên, giảm I/O DB |
| Alert | AlertManager | Gửi cảnh báo Telegram + Webhook khi phát hiện xâm phạm |
| Message Bus | ZeroMQ (IPC Unix socket) | Zero-copy transport giữa Ingress / Core |
| Monitoring | Prometheus + Grafana | FPS, GPU%, VRAM, queue depth, recognition rate |
| API Backend | FastAPI (Python) | REST endpoint CRUD camera/user/event, stream proxy URL |
| Frontend | React SPA (Nginx) | Dashboard giám sát và quản lý |
| Container | Docker + Docker Compose V2 | Orchestration toàn bộ stack |

---

## ⚙️ Yêu cầu hệ thống

### Phần cứng

| Thành phần | Tối thiểu | Khuyến nghị (10+ camera) |
|------------|-----------|--------------------------|
| GPU | RTX 3060 12GB VRAM | RTX 3090 / A4000 24GB+ |
| CPU | 8-core | 16-core+ (ONNX parallel) |
| RAM | 32GB DDR4 | 64GB DDR4 |
| Storage | 500GB NVMe SSD | 2TB NVMe (logs + ảnh crop) |
| Network | 1 Gbps LAN | 10 Gbps (camera farm lớn) |
| OS | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |

### Phần mềm

- Docker Engine 24.x + Docker Compose V2
- NVIDIA Driver ≥ 525 + `nvidia-docker2` (NVIDIA Container Toolkit)
- CUDA ≥ 12.0 (tương thích DeepStream 7.x)
- PostgreSQL 16+ với extension `pgvector` ≥ 0.7
- Redis 7.x

---

## 🏁 Cài đặt và chạy hệ thống

### Bước 1 — Bật NVIDIA Persistence Mode (bắt buộc trước tiên)

```bash
sudo systemctl enable nvidia-persistenced
sudo systemctl start nvidia-persistenced

# Kiểm tra
nvidia-smi --query-gpu=persistence_mode --format=csv,noheader
# Output mong đợi: Enabled
```

> ⚠️ **Bắt buộc.** Nếu bỏ qua bước này, pipeline sẽ deadlock khi màn hình host bị lock/sleep.  
> Xem chi tiết: [`docs/Architecture.md#troubleshooting`](docs/Architecture.md#4-troubleshooting--deadlocks)

### Bước 2 — Tải các model AI

```bash
pip install ultralytics huggingface_hub insightface

# Tải SCRFD, ArcFace R100, YOLOv8s/n, PaddleOCR, MiniFASNet
python scripts/download_models.py
```

*Models lưu tại `models/` dạng ONNX. TensorRT engine sẽ được build tự động lần đầu khởi chạy container.*

### Bước 3 — Khởi động stack

```bash
# Khởi động toàn bộ (Postgres + pgvector, Redis, go2rtc, AI pipeline, API backend, Grafana)
docker compose up -d --build

# Theo dõi log AI pipeline
docker compose logs -f savant-ai-core

# Theo dõi RTSP ingress (camera connections)
docker compose logs -f savant-rtsp-ingress

# Theo dõi go2rtc stream sync
docker compose logs -f go2rtc-sync

# Dừng hoàn toàn
docker compose down
```

### Bước 4 — Khởi tạo database

```bash
# DB migration được chạy tự động khi startup qua db-init service.
# Kiểm tra sau khi containers ready:
docker compose exec postgres psql -U svpro_user -d svpro_db -c "\dt"
```

---

## 🎛 Cấu hình pipeline

File cấu hình chính: `module/module.yml`

| Thông số | Giá trị mặc định | Mô tả |
|----------|-----------------|-------|
| Độ phân giải | 2592 × 1944 | Frame resolution tối đa |
| Primary Detector | YOLOv8s TensorRT FP16 | Phát hiện Vehicle & Person |
| LPR Plate Model | YOLOv8n ONNX | Dò bounding box biển số |
| FR Face Model | SCRFD-10GF ONNX | Face detection + 5-landmark |
| FR Embedding | ArcFace R100 ONNX | 512-dim vector extraction |
| LPR Threshold (plate det.) | 0.50 | Confidence tối thiểu dò biển |
| LPR Threshold (OCR) | 0.60 | Confidence tối thiểu đọc ký tự |
| FR Threshold (SCRFD) | 0.50 | Confidence tối thiểu dò mặt |
| FR Threshold (cosine) | 0.60 | Similarity tối thiểu để chấp nhận match |
| FR Min Face Size | 40 × 40 px | Bỏ qua mặt quá nhỏ/mờ |
| OCR Temporal Window | 10 frames | Vote majority để lọc nhiễu biển số |
| Stranger TTL | 7 ngày | Thời gian lưu `guest_faces` |
| ROI Eval Cron | 23:50 hàng ngày | Tự động tinh chỉnh ROI zones |
| AUTO_APPLY_ROI | `false` | Tự động ghi đè `module.yml` sau ROI eval |
| Output Video | H.264 NVENC | Hardware encode qua NVIDIA NVENC |

---

## 🧾 Output Metadata JSON

Mọi sự kiện nhận diện thành công lưu về `./Detect/{camera}/{date}/{category}/` gồm ảnh crop + file JSON.

### Face Recognition Event

```json
{
  "event_id": "uuid-v4",
  "timestamp": "2026-03-25T07:26:10+07:00",
  "trace_id": "a1b2c3d4",
  "source_id": "cam_gate_01",
  "label": "person",
  "person_id": "EMP-10293",
  "name": "Nguyen Van A",
  "role": "staff",
  "face_confidence": 0.94,
  "match_score": 0.88,
  "is_stranger": false,
  "liveness_score": 0.97,
  "face_quality": {
    "blur_score": 0.92,
    "pose_yaw_deg": 8.5,
    "illumination": 0.85
  },
  "person_bbox": { "x1": 800, "y1": 200, "x2": 1100, "y2": 750 },
  "face_bbox": { "x1": 100, "y1": 20, "x2": 180, "y2": 100 },
  "image_path": "./Detect/cam_gate_01/2026-03-25/face/EMP-10293_07-26-10.jpg",
  "access_granted": true,
  "zone": "Cong chinh"
}
```

Với **Người lạ**, trường `person_id` sẽ là `STRANGER_A1B2C3` và `is_stranger: true`.

### LPR Event

```json
{
  "event_id": "uuid-v4",
  "timestamp": "2026-03-25T07:26:15+07:00",
  "source_id": "cam_parking_01",
  "label": "plate",
  "plate_number": "51A-12345",
  "plate_raw": "51A12345",
  "vehicle_type": "car",
  "ocr_confidence": 0.92,
  "detection_confidence": 0.87,
  "is_registered": true,
  "owner_name": "Tran Thi B",
  "is_blacklisted": false,
  "vehicle_bbox": { "x1": 400, "y1": 500, "x2": 900, "y2": 850 },
  "plate_bbox": { "x1": 450, "y1": 720, "x2": 750, "y2": 790 },
  "image_path": "./Detect/cam_parking_01/2026-03-25/plate/51A12345_07-26-15.jpg"
}
```

> Chi tiết schema đầy đủ: [`docs/Face_Recognition.md`](docs/Face_Recognition.md)

---

## 🎯 Hệ thống đánh giá ROI Autopilot

SV-PRO kế thừa tiến trình `savant-roi-eval` từ `vms-savant`, hoạt động như một cron job độc lập mỗi đêm lúc **23:50**:

1. Quét toàn bộ dữ liệu `/Detect/` trong **3 ngày** qua.
2. Xây dựng **Heatmap lưới 50×50px** theo vị trí BBox nhận diện thành công.
3. Khoanh vùng có **Success Rate > 50%** — loại bỏ vùng quá tối, góc nghiêng, quá xa camera.
4. Nếu `AUTO_APPLY_ROI=true`: tự động ghi đè tham số `roi_zones` vào `module.yml`.
5. Export báo cáo sang `/reports/roi_YYYYMMDD.json`.

---

## 📊 Giám sát & Monitoring

Grafana dashboard tại `http://localhost:3000` (mặc định user/pass: `admin/svpro`):

| Panel | Metric | Ngưỡng cảnh báo |
|-------|--------|----------------|
| Pipeline FPS | FPS thực tế mỗi camera | < 10 FPS → Warning |
| GPU Utilization | % GPU compute | > 95% kéo dài > 60s → Alert |
| VRAM Usage | MB VRAM đang dùng | > 90% max → Alert |
| ZMQ Queue Depth | Số frame đang chờ trong buffer | > 80 frames → Warning |
| Face Match Rate | % face được match thành công | < 70% → Alert |
| Stranger Count | Số stranger mới trong 1h | Dashboard only |
| OCR Accuracy | % biển số đọc đúng (có ground truth) | Dashboard only |

---

## ⚠️ Known Issues & Troubleshooting

### GPU Deadlock khi màn hình lock

**Triệu chứng:** AI Core treo, ZMQ queue tràn, log báo `Cannot accomplish in current state`.

**Nguyên nhân:** GPU Driver bị OS suspended khi màn hình lock → GStreamer phát EOS Storm → ZMQ buffer overflow.

**Giải pháp:** Xem [`docs/Architecture.md#troubleshooting`](docs/Architecture.md#4-troubleshooting--deadlocks) — bật NVIDIA Persistence Mode.

### TensorRT engine build chậm lần đầu

**Triệu chứng:** Container `savant-ai-core` khởi động mất 5–10 phút.

**Nguyên nhân:** TensorRT đang compile & optimize model từ ONNX sang `.engine` file cho GPU hiện tại.

**Giải pháp:** Bình thường, chỉ xảy ra lần đầu. Engine được cache tại `models/engine/`. Không kill container trong quá trình này.

### pgvector query chậm

**Triệu chứng:** Face matching latency > 100ms.

**Nguyên nhân:** HNSW index chưa được tạo hoặc `ef_search` quá thấp.

**Giải pháp:**
```sql
-- Tạo lại HNSW index
CREATE INDEX CONCURRENTLY idx_face_hnsw ON users
USING hnsw (face_embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Tăng ef_search khi query (accuracy vs speed trade-off)
SET hnsw.ef_search = 100;
```

---

## 📚 Tài liệu liên quan

| File | Nội dung |
|------|----------|
| [`docs/Architecture.md`](docs/Architecture.md) | Kiến trúc hệ thống, ZMQ data flow, Dual-AI pipeline, Troubleshooting |
| [`docs/Face_Recognition.md`](docs/Face_Recognition.md) | FR pipeline deep-dive: SCRFD, ArcFace, Stranger Tracking, Anti-Spoofing |
| [`docs/DATABASE.md`](docs/DATABASE.md) | Schema PostgreSQL, pgvector HNSW index, SQL query mẫu |
| [`docs/LPR_Accuracy_Improvements.md`](docs/LPR_Accuracy_Improvements.md) | LPR accuracy: OCR smoothing/normalization/ROI (kế thừa từ vms-savant) |
| [`docs/Reliability_Backpressure_ZMQ_EOS_Deadlock.md`](docs/Reliability_Backpressure_ZMQ_EOS_Deadlock.md) | Reliability & backpressure: ZMQ HWM, EOS guard, deadlock runbook |
| [`docs/Runbook_Reliability_Restart.md`](docs/Runbook_Reliability_Restart.md) | Runbook symptom → check → restart step-by-step |
| [`docs/ControlPlane_Orchestration_vmsPro.md`](docs/ControlPlane_Orchestration_vmsPro.md) | Control plane orchestration (kế thừa tinh thần vmsPro) |
| [`docs/Telemetry_Metrics_DropReason_Contract.md`](docs/Telemetry_Metrics_DropReason_Contract.md) | Contract metrics + drop reason codes + trace_id log |
| [`docs/Project_Plan.md`](docs/Project_Plan.md) | Roadmap 5 Sprint, task list, cải tiến kỹ thuật theo sprint |

---

*SV-PRO — Kế thừa tinh hoa VMS-Savant, mở rộng với High-Performance Dual-AI Pipeline.*  
*© 2026 SV-PRO Project. Tài liệu phiên bản 1.1.*
