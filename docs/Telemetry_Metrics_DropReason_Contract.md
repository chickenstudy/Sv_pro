## Telemetry Contract: Metrics + Drop Reason Codes (Ingress ↔ Egress ↔ AI Core)

Tài liệu này định nghĩa “contract” để các đội triển khai ingress/ai-core/egress có cách đo thống nhất:

- metric names, units, labels
- drop reason codes (mã lý do drop) dùng chung giữa các tầng
- log fields tối thiểu để truy vết bằng `trace_id`

---

## 1. Metric contract (Prometheus naming)

> Khuyến nghị dùng prefix `svpro_` và nhất quán labels.

### 1.1 Labels chuẩn

- `camera_id` : tương ứng `source_id` (khóa trong ROI zones)
- `component` : `ingress | aicore | json_egress | video_egress`
- `model` : tên model (ví dụ `plate_detector | ocr | scrfd | arcface`), nếu không áp dụng thì để `model="none"`
- `result` : `ok | dropped | error` (tùy metric)

### 1.2 Danh sách metric tối thiểu

1. Ingress FPS
   - `svpro_ingress_fps{camera_id}`
   - unit: frames/sec
   - nghĩa: FPS thực tế decode+đẩy vào ZMQ

2. AI core queue depth (backpressure)
   - `svpro_aicore_queue_depth{camera_id}`
   - unit: messages (hoặc frames) trong buffer
   - nghĩa: backlog tại điểm AI core nhận yêu cầu/frame

3. Ingress send latency & timeout count
   - `svpro_ingress_send_latency_ms{camera_id}`
   - unit: ms
   - `svpro_ingress_timeout_total{camera_id,drop_reason}`
   - unit: count

4. Drop counters (frame/event)
   - `svpro_dropped_total{camera_id,component,drop_reason}`
   - unit: count

5. Egress JSON write throughput
   - `svpro_egress_json_rate{camera_id}`
   - unit: events/sec

6. Egress writer queue depth (nếu có)
   - `svpro_egress_writer_queue_depth{camera_id}`
   - unit: tasks/messages

7. Inference latency (P50/P95 là lý tưởng)
   - `svpro_aicore_inference_ms{camera_id,model}`
   - unit: ms

---

## 2. Drop Reason Codes (chuẩn hóa taxonomy)

Drop reason codes dùng chung cho cả ingress/ai-core/egress để dễ aggregate.

### 2.1 Bộ mã drop chính (transport/backpressure)

- `queue_full`  
  Nguyên nhân: HWM/buffer đầy (không nhận được ack kịp / backlog vượt ngưỡng)
- `send_timeout`  
  Nguyên nhân: REQ/REP send timeout (sndtimeo) và chính sách DROP kích hoạt
- `subscriber_lag`  
  Nguyên nhân: PUB/SUB subscriber bị lag, egress không theo kịp → drop cũ nhất
- `eos_storm_guarded`  
  Nguyên nhân: EOS storm bị guard → flush/drop và không forward EOS vào ZMQ
- `socket_state_error`  
  Nguyên nhân: lỗi trạng thái socket/exception khi gửi/nhận (ví dụ “Cannot accomplish…”)

### 2.2 Bộ mã drop bổ sung (egress/internal writers)

- `disk_full`  
  Nguyên nhân: disk full / write failed
- `writer_queue_full`  
  Nguyên nhân: queue writer nội bộ đầy → dropping event/crop
- `yaml_or_config_invalid`  
  Nguyên nhân: module config invalid → không parse được roi_zones/thresholds

---

## 3. Log contract (tối thiểu)

Khuyến nghị mỗi component log ít nhất các field sau (dưới dạng JSON log càng tốt):

- `timestamp` : ISO8601
- `component` : một trong `ingress | aicore | json_egress | video_egress`
- `camera_id`
- `trace_id` : propagate từ ingress
- `event` : `frame_ingress | aicore_inference_start | aicore_inference_end | json_write | drop`
- `queue_depth` : số backlog (nếu áp dụng)
- `drop_reason` : theo contract (nếu event là drop)
- `drop_count` : count (nếu event tổng hợp)
- `error` : string message (nếu có)

Ví dụ log drop:

```json
{
  "component": "ingress",
  "camera_id": "cam_01",
  "trace_id": "a1b2c3d4-...",
  "event": "drop",
  "drop_reason": "send_timeout",
  "queue_depth": 120
}
```

---

## 4. Propagation rule cho trace_id

- `trace_id` phải được generate trong `savant-video-ingress`
- đính kèm theo frame metadata vào ZMQ request
- `savant-ai-core` phải log trace_id trước và sau inference
- `savant-json-egress`/`savant-video-egress` phải log trace_id khi ghi output

Nếu không thể propagate ở cấp frame:

- fallback: generate `trace_id` từ `frame_seq` + `camera_id` ở ai-core

