# Nhận diện Khuôn mặt (Face Recognition) — Deep Dive

SV-PRO mở rộng module lõi nhận diện biển số (`vms-savant`) bằng cách bổ sung luồng phân tích và nhận diện danh tính con người. Module FR hỗ trợ bảo mật nội bộ, hệ thống kiểm soát ra vào (access control), và theo dõi đối tượng chưa đăng ký (Stranger Tracking).

---

## Mục lục

1. [Tổng quan pipeline FR](#1-tổng-quan-pipeline-fr)
2. [Bảng thuật toán AI cấu thành](#2-bảng-thuật-toán-ai-cấu-thành)
3. [Giai đoạn hoạt động tại Runtime](#3-giai-đoạn-hoạt-động-tại-runtime)
4. [Face Quality Filter — Lọc chất lượng trước Embedding](#4-face-quality-filter--lọc-chất-lượng-trước-embedding)
5. [Anti-Spoofing (Liveness Detection)](#5-anti-spoofing-liveness-detection)
6. [Face Alignment — Chuẩn hóa đầu vào ArcFace](#6-face-alignment--chuẩn-hóa-đầu-vào-arcface)
7. [Embedding & Matching — Cosine Similarity](#7-embedding--matching--cosine-similarity)
8. [Cơ chế Redis Cache](#8-cơ-chế-redis-cache)
9. [Stranger Tracking & Cấp phát ID](#9-stranger-tracking--cấp-phát-id)
10. [Federated Re-ID (Multi-camera Stranger Merge)](#10-federated-re-id-multi-camera-stranger-merge)
11. [DeepStream NvDCF Tracker Integration](#11-deepstream-nvdcf-tracker-integration)
12. [Auto-Tuning ROI Heatmap](#12-auto-tuning-roi-heatmap)
13. [Cấu hình & Thresholds](#13-cấu-hình--thresholds)
14. [Hiệu năng & Benchmarks](#14-hiệu-năng--benchmarks)

---

## 1. Tổng quan pipeline FR

```
Full Frame
    │
    ▼
[YOLOv8s TensorRT — Person Detection]
    │
    ▼  (chỉ xử lý person trong ROI Zone hợp lệ)
[Crop Person Region]
    │
    ▼
[Face Quality Pre-filter]   ← Laplacian blur, pose angle, illumination
    │  Pass
    ▼
[SCRFD-10GF — Face Detection + 5-point Landmark]
    │
    ▼
[Anti-Spoofing — MiniFASNet]   ← Liveness check
    │  Passed liveness
    ▼
[AffineTransform — Face Alignment 112×112px]
    │
    ▼
[ArcFace R100 — 512-dim Embedding Extraction]
    │
    ├──► [Redis Cache Lookup]
    │         │ Hit (score > 0.80)
    │         └──► Return Person_ID (~1ms)
    │
    │    Cache Miss
    ▼
[gRPC → sv-api-backend → pgvector HNSW Query]
    │
    ├── similarity > 0.60 ──► Accept → Person_ID + metadata
    │
    └── similarity ≤ 0.60 ──► Stranger Logic
                                  │
                                  ▼
                          STR_<SHA256[:8]>
                          NvDCF Tracker binding
                          guest_faces DB insert
```

---

## 2. Bảng thuật toán AI cấu thành

| Nhiệm vụ | Model | Runtime | Latency | Ghi chú kỹ thuật |
|----------|-------|---------|---------|------------------|
| Person Detection | YOLOv8s (TensorRT FP16) | GPU (nvinfer) | ~5ms/frame | Chia sẻ với LPR pipeline, không duplicate model |
| Face Detection & Landmark | SCRFD-10GF | ONNX Runtime (CPU/GPU nhỏ) | ~8ms/person ROI | 5-point: 2 mắt, 1 mũi, 2 khóe miệng |
| Face Alignment | AffineTransform (NumPy + OpenCV) | CPU | ~0.5ms | Rotate & crop 112×112px cho ArcFace input |
| Liveness / Anti-Spoofing | MiniFASNet (MN3) | ONNX Runtime | ~3ms/face | Phát hiện ảnh in, màn hình giả mạo |
| Face Embedding | ArcFace R100 | ONNX Runtime CPU (~15ms) hoặc TRT GPU (~3ms) | 3–15ms/face | Float32 vector 512 chiều, L2-normalized |
| Cache Matching | Redis (in-memory) | RAM | ~1ms | Hot-set nhân viên thường trú |
| Vector DB Search | pgvector HNSW | PostgreSQL 16 | ~15ms (DB hit) | Cosine similarity, `ef_search=100` |
| Object Tracking | DeepStream NvDCF | GPU | N/A | Giữ ID ổn định qua các frame liên tiếp |

---

## 3. Giai đoạn hoạt động tại Runtime

Vòng đời xử lý mỗi frame trong `src/fr/face_recognizer.py` (Savant PyFunc):

### Bước 1 — ROI Zone Filtering
```python
# Chỉ xử lý person có CenterPoint nằm trong vùng ROI hợp lệ
center_x = (bbox.x1 + bbox.x2) / 2
center_y = (bbox.y1 + bbox.y2) / 2
if not roi_zone.contains(center_x, center_y):
    continue  # Bỏ qua người ở rìa frame / vùng khuất
```

### Bước 2 — Face Quality Pre-filter (xem mục 4)

### Bước 3 — SCRFD Face Detection
```python
# Input: person_region crop (variable size)
# Output: face_bbox (x1,y1,x2,y2) + landmarks (5 điểm Float32)
faces = scrfd_model.detect(person_crop, threshold=0.50)
if not faces:
    continue  # Không tìm thấy mặt trong vùng người
```

### Bước 4 — Anti-Spoofing (xem mục 5)

### Bước 5 — Face Alignment (xem mục 6)

### Bước 6 — ArcFace Embedding
```python
# Input: aligned_face 112×112×3 RGB Float32
# Output: embedding vector 512-dim Float32, L2-normalized
embedding = arcface_model.get_embedding(aligned_face)
embedding = embedding / np.linalg.norm(embedding)  # L2 normalize
```

### Bước 7 — Redis Cache Lookup
```python
cache_key = f"face_embed:{object_tracker_id}"
cached = redis_client.get(cache_key)
if cached:
    person_data = deserialize(cached)
    if person_data["score"] > 0.80:
        return person_data  # Cache hit, ~1ms
```

### Bước 8 — pgvector Query (cache miss)
```python
# Gửi gRPC request đến sv-api-backend
response = face_api_stub.MatchFace(
    MatchRequest(embedding=embedding.tolist(), threshold=0.60)
)
```

### Bước 9 — Decision & Metadata Assignment
```python
if response.similarity > COSINE_THRESHOLD:
    # Known person
    obj.set_attribute("person_id", response.person_id)
    obj.set_attribute("name", response.name)
    obj.set_attribute("match_score", response.similarity)
    # Cập nhật Redis cache
    redis_client.setex(cache_key, 300, serialize(response))
else:
    # Stranger
    stranger_id = "STR_" + hashlib.sha256(embedding.tobytes()).hexdigest()[:8]
    obj.set_attribute("person_id", stranger_id)
    obj.set_attribute("is_stranger", True)
```

### Bước 10 — NvDCF Tracker Binding
```python
# DeepStream Tracker giữ ID ổn định qua các frame
# Không query lại DB cho cùng object_id đã được gán
tracker.bind_id(object_tracker_id, person_id)
```

---

## 4. Face Quality Filter — Lọc chất lượng trước Embedding

Lọc sớm các khuôn mặt chất lượng thấp để tiết kiệm tài nguyên và giảm false-positive:

### 4.1 Kích thước tối thiểu

```python
face_w = face_bbox.x2 - face_bbox.x1
face_h = face_bbox.y2 - face_bbox.y1
if face_w < 40 or face_h < 40:
    skip("face_too_small")
```

### 4.2 Blur Score (Laplacian Variance)

```python
gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
if blur_score < 50:   # Threshold thực nghiệm
    skip("face_too_blurry")
```

### 4.3 Pose Angle (từ SCRFD Landmarks)

```python
# Tính góc yaw từ vị trí 2 mắt và mũi
left_eye, right_eye, nose = landmarks[0], landmarks[1], landmarks[2]
yaw = estimate_yaw(left_eye, right_eye, nose)
pitch = estimate_pitch(landmarks)
if abs(yaw) > 30 or abs(pitch) > 25:
    skip("pose_too_extreme")
```

### 4.4 Illumination Score

```python
hsv = cv2.cvtColor(face_crop, cv2.COLOR_BGR2HSV)
brightness = hsv[:,:,2].mean() / 255.0
if brightness < 0.25:
    skip("too_dark")
if brightness > 0.95:
    skip("overexposed")
```

### Tổng hợp Quality Score

```python
quality_score = (
    min(blur_score / 200, 1.0) * 0.4 +    # blur weight 40%
    (1 - abs(yaw) / 90) * 0.35 +           # pose weight 35%
    brightness_score * 0.25                 # illumination weight 25%
)
if quality_score < 0.50:
    skip("low_quality_composite")
```

---

## 5. Anti-Spoofing (Liveness Detection)

**Mục tiêu:** Ngăn chặn kẻ giả mạo dùng ảnh in, điện thoại, hoặc màn hình máy tính để đánh lừa hệ thống.

### Model: MiniFASNet (MN3)

- Architecture: MobileNetV3-inspired, tối ưu cho embedded/edge
- Input: 80×80px face crop
- Output: `[spoof_prob, real_prob]` softmax
- Latency: ~3ms/face (CPU ONNX Runtime)
- File: `models/anti_spoof/minifasnet_mn3.onnx`

### Ngưỡng quyết định

```python
liveness_score = live_prob  # real_prob từ softmax output
LIVENESS_THRESHOLD = 0.50

if liveness_score < LIVENESS_THRESHOLD:
    # Reject: ghi log spoof attempt
    log_spoof_attempt(camera_id, face_bbox, liveness_score)
    skip("spoof_detected")

# Ghi liveness_score vào JSON output để audit
obj.set_attribute("liveness_score", liveness_score)
```

### Khi nào bật/tắt Anti-Spoofing

| Môi trường | Khuyến nghị |
|------------|-------------|
| Kiểm soát cửa (access control) | **Bắt buộc bật** |
| Camera hành lang / giám sát | Có thể tắt để tiết kiệm CPU |
| Ngoài trời (ánh sáng biến động) | Hạ threshold xuống 0.40 |

Cấu hình trong `module.yml`:
```yaml
face_recognizer:
  anti_spoofing:
    enabled: true
    threshold: 0.50
    log_attempts: true
```

---

## 6. Face Alignment — Chuẩn hóa đầu vào ArcFace

ArcFace yêu cầu khuôn mặt được căn chỉnh chuẩn (aligned) về 112×112px. Nếu bỏ qua bước này, accuracy giảm đáng kể với khuôn mặt nghiêng.

### Quy trình AffineTransform

```python
# Tọa độ chuẩn ArcFace (reference points tại 112×112)
ARCFACE_REF_POINTS = np.array([
    [38.29, 51.70],   # Mắt trái
    [73.53, 51.50],   # Mắt phải
    [56.02, 71.73],   # Mũi
    [41.55, 92.37],   # Khóe miệng trái
    [70.73, 92.26],   # Khóe miệng phải
], dtype=np.float32)

# Tính AffineTransform từ 5 điểm SCRFD landmark
M = cv2.estimateAffinePartial2D(
    src_pts=scrfd_landmarks,
    dst_pts=ARCFACE_REF_POINTS
)[0]

# Warp về kích thước chuẩn
aligned_face = cv2.warpAffine(
    face_crop, M, (112, 112),
    flags=cv2.INTER_LINEAR,
    borderMode=cv2.BORDER_REFLECT
)
```

---

## 7. Embedding & Matching — Cosine Similarity

### Vector đặc trưng

- **Model:** ArcFace R100 (iresnet100 backbone)
- **Dimension:** 512 chiều Float32
- **Normalization:** L2-normalized (unit vector)
- **Metric:** Cosine Similarity = `1 - cosine_distance`

### SQL Query pgvector

```sql
-- Tìm khuôn mặt khớp nhất (< 2ms với HNSW index)
SELECT
    u.id,
    u.name,
    u.role,
    1 - (u.face_embedding <=> $1::vector) AS similarity
FROM users u
WHERE
    u.embedding_version = 'arcface-r100-v1'
    AND 1 - (u.face_embedding <=> $1::vector) > 0.60
ORDER BY u.face_embedding <=> $1::vector
LIMIT 1;
```

### Hướng dẫn chọn Threshold

| Threshold | Trade-off | Khuyến nghị use case |
|-----------|-----------|---------------------|
| 0.75+ | Rất ít false-positive, có thể bỏ sót nhân viên đeo khẩu trang | Môi trường bảo mật cao, cần chứng cứ rõ ràng |
| **0.65** | Cân bằng tốt nhất (production default) | **Kiểm soát cửa thông thường** |
| 0.60 | Nhận dạng tốt hơn điều kiện xấu, tăng false-positive nhẹ | Camera ngoài trời, ánh sáng biến động |
| < 0.55 | Không khuyến nghị | Quá nhiều false-positive |

---

## 8. Cơ chế Redis Cache

### Chiến lược cache hai tầng

```
Tầng 1 — Process-local LRU cache (RAM Python):
    - LRU dict, capacity = 1000 entries
    - TTL = 60 giây
    - Lookup ~0.01ms
    - Dành cho: người xuất hiện liên tục trong cùng session

Tầng 2 — Redis shared cache:
    - Capacity: toàn bộ nhân viên (prefetch khi startup)
    - TTL = 5 phút (auto-refresh khi hit)
    - Lookup ~1ms
    - Key format: "faceembed:{person_id}"
    - Value: msgpack({person_id, name, role, score_threshold})
```

### Prefetch khi startup

```python
# Khi sv-api-backend khởi động: load toàn bộ staff vào Redis
def prefetch_staff_embeddings():
    staff = db.query("SELECT id, name, face_embedding FROM users WHERE role='staff'")
    pipe = redis.pipeline()
    for user in staff:
        key = f"faceembed:{user.id}"
        pipe.setex(key, 300, msgpack.dumps(user))
    pipe.execute()
    logger.info(f"Prefetched {len(staff)} staff embeddings to Redis")
```

### Cache Invalidation

```python
# Khi admin cập nhật face embedding của user
def update_face(user_id, new_embedding):
    db.update(user_id, new_embedding)
    redis.delete(f"faceembed:{user_id}")  # Invalidate cache
    # Cache sẽ được re-populate từ DB trong lần query tiếp theo
```

---

## 9. Stranger Tracking & Cấp phát ID

### Quy trình đầy đủ

```
Khuôn mặt mới, similarity ≤ 0.60
            │
            ▼
Quality check: blur > 0.7 AND pose < 20° AND face_size > 60px?
            │
         NO │                         YES │
            ▼                             ▼
      Bỏ qua (skip)       stranger_id = "STR_" + SHA256(embedding.tobytes())[:8]
                                          │
                                          ▼
                             Redis: SET stranger:{stranger_id} embedding EX 604800
                             (TTL = 7 ngày = 604800 giây)
                                          │
                                          ▼
                             NvDCF Tracker: bind(object_id → stranger_id)
                                          │
                                          ▼
                             Đếm số frame chất lượng tốt của stranger này
                                          │
                               COUNT >= 3 │
                                          ▼
                             INSERT INTO guest_faces
                             (stranger_id, embedding, first_seen, last_seen, camera_id)
                                          │
                                          ▼
                             Dashboard hiển thị: "Khách lạ STR_A1B2C3"
                                          │
                               Operator Action │
                                          ▼
                             Register → INSERT INTO users (chuyển thành user chính thức)
                             OR Blacklist → Gắn role='blacklist', kích hoạt alert
```

### Schema bảng `guest_faces`

```sql
CREATE TABLE guest_faces (
    stranger_id  VARCHAR(20) PRIMARY KEY,  -- STR_XXXXXXXX
    embedding    VECTOR(512) NOT NULL,
    first_seen   TIMESTAMPTZ DEFAULT NOW(),
    last_seen    TIMESTAMPTZ DEFAULT NOW(),
    camera_id    INTEGER REFERENCES cameras(id),
    frame_count  INTEGER DEFAULT 1,        -- Số frame chất lượng đã ghi nhận
    is_registered BOOLEAN DEFAULT FALSE,   -- True sau khi operator register
    expires_at   TIMESTAMPTZ DEFAULT NOW() + INTERVAL '7 days'
);

-- Index để tìm stranger gần đây trên mỗi camera
CREATE INDEX idx_guest_camera_time ON guest_faces (camera_id, last_seen DESC);
```

---

## 10. Federated Re-ID (Multi-camera Stranger Merge)

**Vấn đề:** Cùng một người lạ xuất hiện ở camera A rồi camera B → hệ thống tạo 2 stranger profile riêng biệt `STR_A1B2C3` và `STR_D4E5F6`.

**Giải pháp:** Background job chạy mỗi 5 phút, tìm các stranger profile có embedding gần nhau (cosine > 0.60) trong cùng khoảng thời gian:

```python
# Background job: merge stranger profiles across cameras
def federated_reid_job():
    recent_strangers = db.query("""
        SELECT stranger_id, embedding, camera_id, last_seen
        FROM guest_faces
        WHERE last_seen > NOW() - INTERVAL '10 minutes'
        ORDER BY last_seen DESC
    """)

    for i, s1 in enumerate(recent_strangers):
        for s2 in recent_strangers[i+1:]:
            # Không merge cùng camera
            if s1.camera_id == s2.camera_id:
                continue
            # Tính cosine similarity
            sim = cosine_similarity(s1.embedding, s2.embedding)
            if sim > 0.60:
                # Merge: giữ ID của stranger xuất hiện trước
                canonical_id = min(s1.stranger_id, s2.stranger_id)
                db.merge_strangers(s1.stranger_id, s2.stranger_id, canonical_id)
                logger.info(f"Merged {s1.stranger_id} + {s2.stranger_id} → {canonical_id}")
```

---

## 11. DeepStream NvDCF Tracker Integration

NvDCF (NVIDIA Discriminative Correlation Filter) là module tracking nội bộ của DeepStream, giữ Object ID ổn định qua các frame dựa trên visual appearance.

### Tại sao cần Tracker

Không có tracker, mỗi 1/30 giây sẽ gửi 1 Vector Search query → 30 queries/giây/person → không khả thi.

Với tracker: chỉ query **1–2 frame đầu tiên** khi object mới xuất hiện. Sau đó tracker bind ID ổn định:

```
Frame 1:  person detected → query pgvector → gán Person_ID="EMP-123"
Frame 2:  NvDCF tracker confidence > 0.70 → dùng lại "EMP-123" (no query)
Frame 3:  NvDCF tracker confidence > 0.70 → dùng lại "EMP-123" (no query)
...
Frame N:  tracker confidence < 0.50 (người bị che khuất) → re-query
```

### Cấu hình NvDCF trong `module.yml`

```yaml
tracker:
  type: NvDCF
  config:
    useColorCues: true
    useMotionPrediction: true
    maxTargetsPerStream: 100
    minDetectorConfidence: 0.50
    reidentificationThreshold: 0.70   # Dưới ngưỡng này → re-query DB
    maxShadowTrackingAge: 30           # Frame, giữ ID khi bị che khuất tạm thời
```

---

## 12. Auto-Tuning ROI Heatmap

Mỗi đêm lúc **23:50**, `savant-roi-eval` tự động tinh chỉnh vùng xử lý FR:

### Quy trình

```python
# 1. Load 3 ngày JSON log
logs = load_detection_logs(days=3, object_type="face")

# 2. Xây dựng Heatmap 50×50 grid
heatmap = np.zeros((50, 50))
for log in logs:
    grid_x = int(log.face_cx / frame_width * 50)
    grid_y = int(log.face_cy / frame_height * 50)
    if log.match_success:
        heatmap[grid_y][grid_x] += 1
    else:
        heatmap[grid_y][grid_x] -= 0.5  # Penalize failed zones

# 3. Tìm vùng có success rate > 50%
success_zones = find_contiguous_zones(heatmap, threshold=0.5)

# 4. Update ROI zones
new_roi = bounding_rect(success_zones)
if AUTO_APPLY_ROI:
    update_module_yml(camera_id, new_roi)

# 5. Export báo cáo
save_report(f"/reports/roi_{today}.json", {
    "camera_id": camera_id,
    "heatmap": heatmap.tolist(),
    "new_roi": new_roi,
    "face_total": len(logs),
    "success_rate": success_rate
})
```

---

## 13. Cấu hình & Thresholds

File: `module/module.yml`, section `face_recognizer`

```yaml
face_recognizer:
  # Detection
  scrfd_model: models/scrfd/scrfd_10g_bnkps.onnx
  scrfd_threshold: 0.50
  min_face_size: 40          # pixels, tối thiểu cả chiều rộng lẫn chiều cao

  # Quality filter
  quality_filter:
    enabled: true
    min_blur_score: 50       # Laplacian variance
    max_pose_yaw_deg: 30
    max_pose_pitch_deg: 25
    min_illumination: 0.25
    composite_threshold: 0.50

  # Anti-spoofing
  anti_spoofing:
    enabled: true
    model: models/anti_spoof/minifasnet_mn3.onnx
    threshold: 0.50
    log_attempts: true

  # Embedding
  arcface_model: models/arcface/r100_arcface.onnx
  embedding_dim: 512

  # Matching
  cosine_threshold: 0.60
  embedding_version: arcface-r100-v1

  # Stranger
  stranger_min_frames: 3     # Số frame chất lượng tốt trước khi lưu DB
  stranger_ttl_days: 7

  # Cache
  redis_cache_ttl_sec: 300
  local_lru_size: 1000
  local_lru_ttl_sec: 60
```

---

## 14. Hiệu năng & Benchmarks

*Đo trên RTX 3090, Ubuntu 22.04, CUDA 12.2, ONNX Runtime 1.17*

| Stage | Latency (ms) | Throughput |
|-------|-------------|------------|
| SCRFD Detection (per person ROI) | ~8ms | ~125 person/s |
| Face Alignment (per face) | ~0.5ms | ~2000 face/s |
| MiniFASNet Anti-Spoofing | ~3ms | ~333 face/s |
| ArcFace Embedding (CPU ONNX) | ~15ms | ~67 face/s |
| ArcFace Embedding (GPU TRT FP16) | ~3ms | ~333 face/s |
| Redis cache lookup | ~1ms | ~1000 face/s |
| pgvector HNSW query (1M faces) | ~15ms | ~67 query/s |
| **End-to-end FR (GPU, cache hit)** | **~20ms** | **~50 face/s** |
| **End-to-end FR (CPU, DB query)** | **~50ms** | **~20 face/s** |

**Khuyến nghị:** Chạy ArcFace trên GPU (TensorRT FP16) khi số face/giây > 15. Với < 10 camera và mật độ người trung bình, CPU ONNX Runtime là đủ.

---

*© 2026 SV-PRO Face Recognition Module Documentation — Phiên bản 1.1*
