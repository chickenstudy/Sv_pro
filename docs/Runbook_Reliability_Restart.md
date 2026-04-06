## Runbook Reliability (Symptom → Check → Restart)

Mục tiêu tài liệu: khi pipeline “im lặng” hoặc có hành vi treo/giảm hiệu năng, hãy xử lý theo trình tự để:

- khôi phục nhanh nhất với ít restart nhất
- tránh restart loop làm mất ổn định toàn hệ thống
- phân biệt rõ lỗi do `ingress` (RTSP/ZMQ input) hay do `ai-core` (GPU/pipeline) hay do `egress` (JSON/video writer)

### Quy ước service names

- `savant-video-ingress` (có thể có nhiều instance: `savant-video-ingress-1`, `savant-video-ingress-2`, …)
- `savant-ai-core` (thường là container `savant-core`)
- `savant-json-egress`
- `savant-video-egress` (annotated RTSP/HLS)
- Các file YAML module và thư mục detect/output theo docs SV-PRO.

---

## 0. Circuit breaker (luật chung trước khi restart)

1. Mọi lần restart phải ghi lại thời điểm + component.
2. Giới hạn restart tối đa: **N lần / 10 phút** (khuyến nghị N=3).
3. Nếu vượt ngưỡng:
   - dừng restart tự động
   - chuyển sang trạng thái `degraded`
   - yêu cầu operator kiểm tra nguyên nhân gốc (GPU/host lock/disk full/RTSP down).

---

## 1. Symptom: JSON egress không tăng (no new events)

### 1.1 Check (5–10 phút)

1. Kiểm tra container status:
   - `docker compose ps`
   - đảm bảo `savant-json-egress` không crash loop
2. Xác nhận “metadata production” từ ai-core có hay không:
   - `docker compose logs --tail 200 savant-ai-core`
   - xem có log inference/processing tiếp tục hay không
3. Kiểm tra ZMQ backlog/queue depth bằng metrics (nếu có Prometheus):
   - xem `svpro_aicore_queue_depth{camera_id=...}`
4. Kiểm tra filesystem/disk:
   - kiểm tra dung lượng `Detect/` và `output/` (nếu disk full → JSON writer dừng)
5. Kiểm tra trace_id:
   - có `trace_id` của frame “mới” chạy qua ingress mà không thấy tương ứng ở json egress không?

### 1.2 Restart steps (ưu tiên ít gián đoạn)

1. Restart **JSON egress trước**:
   - `docker compose restart savant-json-egress`
2. Nếu vẫn không tăng sau 1–2 chu kỳ:
   - Restart **video egress** (để test xem subscriber PUB/SUB hoạt động hay không):
   - `docker compose restart savant-video-egress`
3. Nếu JSON + video egress đều không tăng/không hoạt động:
   - Restart **AI core**:
   - `docker compose restart savant-ai-core`
4. Nếu sau restart AI core vẫn “im lặng” nhưng ingress có FPS:
   - ưu tiên kiểm tra host GPU/persistence mode và EOS storm.

---

## 2. Symptom: AI Core “healthy” nhưng không có progress / log kẹt

Các log điển hình trong SV-PRO docs:

- `Cannot accomplish in current state`

### 2.1 Check

1. Xem log ai-core trong 2–5 phút gần nhất:
   - `docker compose logs --tail 400 savant-ai-core`
2. Kiểm tra host có lock/suspend không:
   - nếu có: deadlock do GPU driver suspend là khả năng cao
3. Kiểm tra EOS storm:
   - ingress logs có nhiều reconnect/EOS liên tục không?
4. Kiểm tra metrics:
   - FPS ingest có còn đều không?
   - queue depth có tăng dần không?

### 2.2 Restart steps

1. Restart **AI core** (có backoff):
   - `docker compose restart savant-ai-core`
2. Nếu lần 1 khôi phục tạm thời nhưng lại treo:
   - đảm bảo **NVIDIA Persistence Mode** đang `Enabled`
   - restart ingress instance để reset RTSP state:
   - `docker compose restart savant-video-ingress`
3. Nếu host đang bị lock:
   - dừng toàn bộ pipeline và xử lý ở OS (tắt suspend/screensaver, bật persistence mode).

---

## 3. Symptom: FPS giảm đột ngột (performance drop)

### 3.1 Check

1. So sánh theo camera:
   - metrics `svpro_ingress_fps{camera_id}` giảm ở tất cả hay chỉ 1 vài camera?
2. Kiểm tra queue depth:
   - `svpro_aicore_queue_depth` tăng mạnh hay không
3. Kiểm tra egress:
   - json write rate giảm (events/sec)
4. Kiểm tra OCR/LPR:
   - nếu có log “save queue full / dropping event” → disk writer bottleneck

### 3.2 Restart steps

1. Nếu chỉ giảm ở 1–2 camera:
   - restart đúng ingress instance camera đó (không restart toàn bộ ai-core)
2. Nếu toàn bộ camera giảm:
   - restart **AI core** (thường reset pipeline congestion)
3. Nếu ai-core không giảm queue nhưng json/video egress là bottleneck:
   - restart egress trước.

---

## 4. Symptom: Container crash loop / restart loop

### 4.1 Check

1. `docker compose ps` để thấy restart count
2. `docker compose logs --tail 200 <service>` để tìm stacktrace/EOF/disk full
3. Kiểm tra disk:
   - nếu disk full → giải phóng dung lượng trước, restart sau
4. Kiểm tra cấu hình:
   - module/module.yml path có đúng không
   - model file có tồn tại không

### 4.2 Restart steps

1. Dừng restart tự động nếu vượt circuit breaker
2. Restart theo thứ tự:
   - egress (JSON/video) → ai-core → ingress
3. Sau mỗi restart, đợi tối thiểu:
   - AI core: 30–60 giây (TensorRT engine build lần đầu có thể lâu hơn)
   - egress: 10–20 giây

---

## 5. Symptom: Suspected EOS storm (RTSP disconnect liên tục)

### 5.1 Check

1. Ingress logs có nhiều reconnect/EOS trong thời gian ngắn không?
2. Queue depth có tăng dần không?
3. `svpro` metrics có drop rate cao không?

### 5.2 Restart steps

1. Restart **ingress instance** trước:
   - `docker compose restart savant-video-ingress`
2. Nếu EOS storm vẫn tiếp diễn:
   - kiểm tra RTSP credentials/network
3. Nếu AI core vẫn bị kẹt:
   - restart AI core sau khi ingress đã ổn định lại.

---

## 6. Checklist “trước khi restart” (nhanh)

- Đã xem queue depth/backlog và drop rate chưa?
- Egress có crash loop không?
- Disk có đầy không?
- Host có lock/suspend không?
- Persistence mode có `Enabled` chưa?

