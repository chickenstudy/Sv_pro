# Kiến trúc Hệ thống SV-PRO (System Architecture)

SV-PRO kế thừa nguyên gốc (1-to-1) kiến trúc phân tán tiến trình qua **ZeroMQ** của hệ thống `vms-savant` và mở rộng thành kiến trúc **Dual-AI Pipeline**.

Tài liệu này đi sâu vào chi tiết dòng dữ liệu, thiết kế mô-đun, các thành tố xử lý bên trong lõi hệ thống và các cải tiến kỹ thuật đề xuất.

---

## Mục lục

1. [Triết lý thiết kế & Nguyên tắc phân tách](#1-triết-lý-thiết-kế--nguyên-tắc-phân-tách)
2. [Dòng dữ liệu qua IPC ZMQ](#2-dòng-dữ-liệu-qua-ipc-zmq)
3. [Thiết kế Dual-AI Pipeline bên trong AI Core](#3-thiết-kế-dual-ai-pipeline-bên-trong-ai-core)
4. [Hoạt động của PyFunc FaceRecognizer](#4-hoạt-động-của-pyfunc-facerecognizer)
5. [Hoạt động của PyFunc PlateOCR](#5-hoạt-động-của-pyfunc-plateocr)
6. [ZMQ Socket — Cấu hình & Giới hạn an toàn](#6-zmq-socket--cấu-hình--giới-hạn-an-toàn)
7. [Multi-GPU Sharding (mở rộng ngang)](#7-multi-gpu-sharding-mở-rộng-ngang)
8. [Observability & Distributed Tracing](#8-observability--distributed-tracing)
9. [Troubleshooting & Deadlocks](#9-troubleshooting--deadlocks)

---

## 1. Triết lý thiết kế & Nguyên tắc phân tách

Hệ thống loại bỏ hoàn toàn cấu trúc **Monolithic** — không gộp chung một Docker Container chứa source, xử lý hình ảnh và đóng gói video streaming. Thay vào đó, SV-PRO tách nhỏ các module để **cô lập trạng thái nghẽn (fault isolation)**:

| Nguyên tắc | Ý nghĩa thực tế |
|------------|----------------|
| **Ingress Isolation** | Camera restart / mất tín hiệu RTSP không làm gián đoạn AI Core hay Egress |
| **AI Core Independence** | Nâng cấp model (đổi weights, đổi TensorRT engine) không cần dừng Egress |
| **Egress Fault Tolerance** | Video-egress crash (OOM, codec lỗi) không mất metadata JSON và không dừng pipeline AI |
| **Independent Scaling** | Có thể chạy nhiều Ingress instance cho nhiều camera mà không nhân bản AI Core |

---

## 2. Dòng dữ liệu qua IPC ZMQ

### 2.1 Sơ đồ đầy đủ

```
RTSP Camera(s)
     │
     ▼
┌─────────────────────────────────────────┐
│       savant-video-ingress (N instances) │
│  GStreamer RTSP src → NVDEC decode       │
│  → ZMQ REQ socket (input-video.ipc)     │
└─────────────────────────────────────────┘
                    │
                    │  ZMQ Request/Reply
                    │  Unix socket: /tmp/sv-ipc/input-video.ipc
                    ▼
┌─────────────────────────────────────────┐
│        savant-ai-core (GPU)             │
│  ZMQ REP ← nhận RAW frame buffer       │
│  DeepStream pipeline xử lý AI          │
│  nhúng metadata vào GstBuffer          │
│  ZMQ PUB → output-video.ipc            │
└─────────────────────────────────────────┘
                    │
                    │  ZMQ Publish/Subscribe
                    │  Unix socket: /tmp/sv-ipc/output-video.ipc
                    ▼
     ┌──────────────┼──────────────────┐
     ▼              ▼                  ▼
[json-egress]  [video-egress-1]  [video-egress-2]
SUB subscriber SUB subscriber    SUB subscriber
JSON → disk    RTSP :554         HLS :888
→ Kafka        (annotated)       (annotated)
→ Elasticsearch
```

### 2.2 Bảng mô tả từng service

| Service | ZMQ Role | Socket | Chức năng |
|---------|----------|--------|-----------|
| `savant-video-ingress` | REQ (sender) | `input-video.ipc` | Thu RTSP bằng GStreamer, chuyển RAW frame vào AI Core |
| `savant-ai-core` | REP (receiver) + PUB (broadcaster) | `input-video.ipc` + `output-video.ipc` | Chạy DeepStream pipeline, inference AI, nhúng metadata, phát kết quả xuống Egress |
| `savant-json-egress` | SUB (subscriber) | `output-video.ipc` | Convert DeepStream metadata → JSON / Kafka / Elasticsearch |
| `savant-video-egress` | SUB (subscriber) | `output-video.ipc` | Render BBox overlay + ID text lên video → RTSP/HLS output |

### 2.3 Ưu điểm mô hình Pub/Sub cho Egress

Vì `output-video.ipc` là **PUB socket**, có thể attach bất kỳ số lượng SUB subscriber nào mà không cần thay đổi AI Core. Trong tương lai có thể thêm:
- `alert-egress`: Subscribe và trigger Telegram/Webhook khi phát hiện blacklist.
- `s3-egress`: Tự động upload ảnh crop lên object storage.
- `analytics-egress`: Ghi dữ liệu vào InfluxDB/TimescaleDB cho analytics dài hạn.

---

## 3. Thiết kế Dual-AI Pipeline bên trong AI Core

Toàn bộ logic xử lý được cấu hình tại `module/module.yml`. Pipeline gồm **2 chặng nối tiếp**:

### Chặng 1: Primary Object Detector (`nvinfer` TensorRT)

```
Full Frame (2592×1944 hoặc 1920×1080)
        │
        ▼
  YOLOv8s TensorRT FP16
  (Batch inference trên GPU)
        │
        ├──► Class: Vehicle (Car, Bus, Motorcycle, Truck)
        │    → Gắn tag: LPR-Pending
        │    → Gửi vào PlateOCR PyFunc
        │
        └──► Class: Person
             → Gắn tag: Face-Pending
             → Gửi vào FaceRecognizer PyFunc
```

**Lý do chia sẻ tầng 1:** YOLOv8s TensorRT chạy 1 lần duy nhất trên full frame — tránh lãng phí VRAM khi phải load 2 model detection riêng biệt cho LPR và FR.

### Chặng 2: PyFunc Plugin — Rẽ nhánh độc lập

```
┌─────────────────────────────────────────────────────────┐
│                   NvDsPyFuncPlugin                       │
│                                                          │
│   [Vehicle Objects]              [Person Objects]        │
│          │                              │                │
│          ▼                              ▼                │
│   src/lpr/plate_ocr.py       src/fr/face_recognizer.py  │
│   (PlateOCR)                 (FaceRecognizer)            │
│                                                          │
│   Non-blocking, chạy song song trên RAM CPU              │
│   (ONNX Runtime) hoặc gRPC đến External Service         │
└─────────────────────────────────────────────────────────┘
```

Hai luồng thực thi **non-blocking** — không cản trở lẫn nhau. Nếu một xe có biển số mờ khiến PlateOCR mất thêm thời gian, FaceRecognizer vẫn xử lý bình thường và ngược lại.

---

## 4. Hoạt động của PyFunc FaceRecognizer

Khác với LPR là tính toán In-memory tại chỗ, FR cần tương tác với cơ sở dữ liệu và hệ thống cache:

```
[NvDsFrame Buffer]
   │
   ├─► Lọc Object: chỉ xử lý name="person" có CenterPoint trong ROI Zone
   │   (Bỏ qua người ở rìa frame, vùng ánh sáng ngược, góc khuất)
   │
   ├─► Face Quality Pre-filter:
   │   - Face BBox < 40×40px  → Skip (quá nhỏ)
   │   - Blur score < 0.5     → Skip (ảnh nhòe, Laplacian variance)
   │   - Pose yaw/pitch > 30° → Skip (quay mặt quá nghiêng)
   │   - Illumination < 0.3   → Skip (quá tối)
   │
   ├─► SCRFD Face Detection:
   │   - Input: person_region crop
   │   - Output: Face BBox + 5-point Landmark (2 mắt, 1 mũi, 2 khóe miệng)
   │
   ├─► Face Alignment (AffineTransform):
   │   - Căn chỉnh dựa trên 2 mắt → chuẩn hóa về 112×112px
   │   - Input chuẩn cho ArcFace R100
   │
   ├─► Anti-Spoofing (MiniFASNet):
   │   - Liveness score < 0.5 → Reject (ảnh in / màn hình giả)
   │   - Latency: ~3ms/face
   │
   ├─► ArcFace R100 Embedding:
   │   - Output: Float32 vector 512 chiều, L2-normalized
   │
   ├─► Redis Cache Lookup (hot-set nhân viên):
   │   - Hit (score > 0.80) → trả về Person_ID ngay (~1ms)
   │   - Miss → gửi gRPC đến sv-api-backend
   │
   ├─► pgvector HNSW Query (cache miss):
   │   SELECT id, name, 1 - (face_embedding <=> $vector) AS sim
   │   FROM users WHERE sim > 0.60
   │   ORDER BY face_embedding <=> $vector LIMIT 1;
   │   Latency: ~15ms
   │
   ├─► Decision:
   │   sim > 0.60  → Accept → gắn Person_ID + Name vào Object Metadata
   │   sim ≤ 0.60  → Stranger → stranger_id = "STR_" + SHA256(vector)[:8]
   │
   └─► NvDCF Tracker Binding:
       Gắn Person_ID / Stranger_ID vào DeepStream Tracker.
       Frame tiếp theo cùng Object → dùng lại Tracker ID, KHÔNG query lại DB.
```

### Cơ chế Stranger Tracking chi tiết

```
Khuôn mặt mới, sim ≤ threshold
        │
        ▼
stranger_id = "STR_" + SHA256(embedding)[:8]
        │
        ▼
Lưu vào Redis tạm: key=stranger_id, TTL=7 ngày
        │
        ├─► Frame 1-2:  Query DB để xác nhận lần đầu
        ├─► Frame 3+:   NvDCF Tracker giữ ID ổn định, không query lại
        │
        ▼
Nếu >= 3 frame chất lượng tốt (blur > 0.7, pose < 20°):
        │
        ▼
Push vector vào bảng guest_faces (PostgreSQL)
        │
        ▼
Backend gán stranger profile → hiển thị trên Dashboard
        │
        ▼
Operator có thể "Register" → chuyển thành user chính thức
```

---

## 5. Hoạt động của PyFunc PlateOCR

```
[NvDsFrame Buffer]
   │
   ├─► Crop Vehicle ROI từ BBox của Object
   │
   ├─► YOLOv8n (ONNX Runtime):
   │   Dò Plate BBox bên trong vehicle crop
   │   Confidence > 0.50 mới tiếp tục
   │
   ├─► Plate Preprocessing:
   │   - CLAHE (Contrast Limited Adaptive Histogram Equalization)
   │   - Upscale lên 128×384px nếu chiều rộng < 128px
   │   - Night Mode: nếu mean brightness < 80 → thêm Histogram EQ
   │
   ├─► Split-line Detection:
   │   - Tỷ lệ H/W > 0.5? → Biển 2 dòng (xe máy)
   │   - Cắt đôi theo midpoint chiều ngang
   │   - OCR riêng từng nửa, ghép lại
   │
   ├─► PaddleOCR v4 (ONNX):
   │   - Đọc ký tự biển số
   │   - Confidence OCR > 0.60 mới lưu
   │
   ├─► Normalize biển số Việt Nam:
   │   - "51A12345"  → "51A-12345"
   │   - "29B1 12345" → "29B1-123.45"
   │   - Loại bỏ ký tự rác (O→0, I→1 khi context rõ ràng)
   │
   ├─► Temporal Smoothing (10-frame window):
   │   Vote majority trên 10 frame gần nhất của cùng Tracker ID
   │   → Chọn kết quả xuất hiện nhiều nhất
   │   → Loại nhiễu đọc sai do ánh đèn nhấp nháy
   │
   └─► Gắn plate_number + confidence vào Object Metadata
```

---

## 6. ZMQ Socket — Cấu hình & Giới hạn an toàn

> ⚠️ **Điểm yếu quan trọng cần cấu hình:** Mặc định ZMQ không giới hạn buffer — nếu AI Core xử lý chậm hơn tốc độ Ingress đẩy vào, RAM sẽ bị chiếm dần và cuối cùng crash OOM silently.

### Cấu hình High-Water Mark (HWM) bắt buộc

Thêm vào cấu hình ZMQ socket của `savant-video-ingress`:

```python
# Giới hạn hàng chờ: tối đa 100 messages (~100 frames)
socket.sndwm = 100      # Sender High-Water Mark
socket.rcvhwm = 100     # Receiver High-Water Mark
socket.sndtimeo = 200   # Timeout gửi: 200ms (không block vô hạn)
```

Chính sách khi đầy buffer:
- **REQ/REP socket (Ingress → AI Core):** Block sender tối đa 200ms, sau đó **DROP frame** và log warning. Tốt hơn là OOM crash.
- **PUB/SUB socket (AI Core → Egress):** DROP frame cũ nhất (FIFO drop) khi subscriber bị lag.

### EOS Storm Guard (bắt buộc triển khai)

Khi camera bị ngắt kết nối đột ngột, GStreamer phát End-of-Stream (EOS) liên tục. Nếu không có guard, hàng chờ ZMQ tràn:

```python
# Trong savant-video-ingress: throttle EOS events
eos_counter = 0
eos_last_reset = time.time()

def on_eos(event):
    global eos_counter, eos_last_reset
    now = time.time()
    if now - eos_last_reset > 1.0:
        eos_counter = 0
        eos_last_reset = now
    eos_counter += 1
    if eos_counter > 5:
        logger.warning("EOS Storm detected — flushing ZMQ queue and reconnecting")
        flush_zmq_queue()
        reconnect_rtsp()
        return  # Không forward EOS vào ZMQ
    forward_eos_to_zmq(event)
```

Chi tiết triển khai/behavior khi đầy buffer và runbook deadlock xem: [`docs/Reliability_Backpressure_ZMQ_EOS_Deadlock.md`](docs/Reliability_Backpressure_ZMQ_EOS_Deadlock.md).

---

## 7. Multi-GPU Sharding (mở rộng ngang)

Với hệ thống > 16 camera, một GPU sẽ đạt giới hạn. Chiến lược sharding:

```
Camera 1-8  →  savant-ingress-group-A  →  savant-ai-core-gpu0  →  egress-group-A
                                           (GPU 0, CUDA:0)
                                           IPC: /tmp/sv-ipc-gpu0/

Camera 9-16 →  savant-ingress-group-B  →  savant-ai-core-gpu1  →  egress-group-B
                                           (GPU 1, CUDA:1)
                                           IPC: /tmp/sv-ipc-gpu1/
```

Mỗi `savant-ai-core` instance bind vào **IPC path riêng biệt** — tránh xung đột socket. Thêm vào `docker-compose.yml`:

```yaml
savant-ai-core-gpu0:
  environment:
    - CUDA_VISIBLE_DEVICES=0
    - ZMQ_INPUT_IPC=/tmp/sv-ipc-gpu0/input-video.ipc
    - ZMQ_OUTPUT_IPC=/tmp/sv-ipc-gpu0/output-video.ipc

savant-ai-core-gpu1:
  environment:
    - CUDA_VISIBLE_DEVICES=1
    - ZMQ_INPUT_IPC=/tmp/sv-ipc-gpu1/input-video.ipc
    - ZMQ_OUTPUT_IPC=/tmp/sv-ipc-gpu1/output-video.ipc
```

---

## 8. Observability & Distributed Tracing

### Metrics (Prometheus)

Mỗi component expose `/metrics` endpoint:

| Metric | Component | Mô tả |
|--------|-----------|-------|
| `svpro_ingress_fps{camera_id}` | Ingress | FPS thực tế mỗi camera |
| `svpro_aicore_queue_depth` | AI Core | Số frame đang chờ trong ZMQ buffer |
| `svpro_aicore_inference_ms{model}` | AI Core | Latency inference từng model (ms) |
| `svpro_fr_match_total{result}` | AI Core | Số lần match: accepted / stranger / skipped |
| `svpro_lpr_ocr_total{result}` | AI Core | Số lần OCR: success / low_confidence / skipped |
| `svpro_gpu_vram_mb` | AI Core | VRAM đang dùng (MB) |
| `svpro_egress_json_total` | JSON Egress | Tổng số event JSON đã ghi ra |

### Distributed Tracing (OpenTelemetry)

Gắn `trace_id` vào mỗi frame từ Ingress, truyền qua toàn pipeline, export sang Jaeger/Tempo:

```python
# Trong savant-video-ingress
trace_id = generate_trace_id()  # UUID v4
frame_metadata["trace_id"] = trace_id
span = tracer.start_span("frame_ingress", attributes={"camera_id": cam_id})
```

Cho phép phân tích latency từng bước: `RTSP receive → ZMQ send → nvinfer → PyFunc → ZMQ pub → JSON write`.

---

## 9. Troubleshooting & Deadlocks

### 9.1 ZMQ Deadlock khi màn hình host bị lock

**Chuỗi sự kiện gây deadlock:**

```
1. OS Host Lock Screen
        ↓
2. NVIDIA GPU Driver → Suspended mode
        ↓
3. GStreamer pipeline mất GPU context
        ↓
4. GStreamer phát EOS liên tục (EOS Storm)
        ↓
5. ZMQ queue tràn buffer (không có HWM)
        ↓
6. AI Core treo: "Cannot accomplish in current state"
        ↓
7. Toàn bộ pipeline đứng, không tự phục hồi
```

**Giải pháp bắt buộc — NVIDIA Persistence Mode:**

```ini
# Tạo file: /etc/systemd/system/nvidia-persistenced.service.d/override.conf
[Service]
ExecStart=
ExecStart=/usr/bin/nvidia-persistenced --user nvidia-persistenced --verbose
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nvidia-persistenced

# Kiểm tra
nvidia-smi --query-gpu=persistence_mode --format=csv,noheader
# Kết quả: Enabled
```

Với Persistence Mode bật, GPU Driver duy trì trạng thái "active" 24/7 bất kể trạng thái màn hình host.

**Giải pháp bổ sung (defense in depth):**
- Tắt screensaver trên host: `gsettings set org.gnome.desktop.session idle-delay 0`
- Tắt suspend: `sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target`
- Bật EOS Storm Guard (xem mục 6)

### 9.2 TensorRT build timeout lần đầu

Nếu container timeout khi build TensorRT engine (> 10 phút), tăng `stop_grace_period` trong `docker-compose.yml`:

```yaml
savant-ai-core:
  stop_grace_period: 30m
```

### 9.3 Cách restart an toàn từng thành phần

```bash
# Restart Ingress (không ảnh hưởng AI Core hay Egress)
docker compose restart savant-video-ingress-1

# Restart JSON Egress (không ảnh hưởng AI pipeline)
docker compose restart savant-json-egress

# Restart AI Core (pipeline tạm dừng, tự phục hồi sau ~30s)
docker compose restart savant-ai-core
```

---

*© 2026 SV-PRO Architecture Documentation — Phiên bản 1.1*
