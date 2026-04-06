## Reliability & Backpressure (SV-PRO)

Tài liệu này chuẩn hóa phần **Reliability / Backpressure / Deadlock prevention** dựa trên:

- “bài học production” trong `vms-savant` (deadlock khi host lock screen, ZMQ queue tràn, EOS storm)
- phần thiết kế trong `sv-pro/docs/Architecture.md`
- tinh thần bounded-channel + timeout + restart “có kiểm soát” trong `vmsPro` (workers chờ health rồi restart)

Mục tiêu: tránh các tình huống sau:

- pipeline “im lặng chết” (no new JSON, no logs rõ ràng)
- OOM do buffer không giới hạn
- deadlock do GPU driver bị suspend
- queue growth không kiểm soát làm tăng latency vô hạn

---

## 1. Backpressure model theo loại socket

Trong kiến trúc ZMQ của Savant:

- Ingress → AI Core dùng **REQ/REP**
- AI Core → Egress dùng **PUB/SUB**

Do đó policy drop phải khác nhau theo socket type.

### 1.1 REQ/REP (Ingress → AI Core)

Khuyến nghị:

- cấu hình `sndwm` / `rcvhwm` ở mức vừa đủ
- cấu hình timeout gửi `sndtimeo` để **không block vô hạn**
- khi buffer đầy:
  - cho ingress block tối đa ~200ms
  - nếu vẫn không được: **DROP frame** + log cảnh báo

Lý do: tốt hơn là mất vài frame hơn là RAM tăng dần và crash.

### 1.2 PUB/SUB (AI Core → Egress)

Khuyến nghị:

- AI Core PUB nên có giới hạn backlog (HWM)
- egress subscriber bị lag thì:
  - **drop frame cũ nhất** để “đuổi kịp” trạng thái hiện tại

Đừng để subscriber lag làm toàn hệ thống tăng queue.

---

## 2. ZMQ HWM & timeouts (bắt buộc)

Thêm vào cấu hình ZMQ socket của `savant-video-ingress` (và tương ứng nơi có send/recv):

```python
# Giới hạn hàng chờ: tối đa 100 messages (~100 frames)
socket.sndwm = 100       # Sender High-Water Mark
socket.rcvhwm = 100      # Receiver High-Water Mark
socket.sndtimeo = 200    # Timeout gửi: 200ms
```

Chính sách khi đầy buffer:

- REQ/REP: block tối đa `sndtimeo`, sau đó DROP frame
- PUB/SUB: drop frame cũ nhất (FIFO drop)

Nếu bạn triển khai ở lớp khác (adapter/container), hãy áp dụng tương đương:

- set HWM cho both sides
- set linger/timeout để tránh “socket stuck forever”

---

## 3. EOS Storm Guard (bắt buộc)

### 3.1 Vì sao cần EOS guard

Khi camera RTSP bị ngắt đột ngột hoặc host lock/suspend, GStreamer có thể bắn EOS liên tục (EOS storm).

Nếu forwarding EOS vào ZMQ hoặc không reset trạng thái:

- ZMQ queue tràn
- AI Core treo / crash loop
- pipeline “không tự hồi phục”

### 3.2 Thuật toán guard (mô tả)

Trong `savant-video-ingress`:

- đếm `eos_counter`
- cứ mỗi giây reset counter
- nếu vượt ngưỡng `> 5 EOS/giây`:
  - flush ZMQ queue (hoặc reset session state)
  - reconnect RTSP
  - KHÔNG forward EOS vào ZMQ

Pseudo:

```python
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
        return

    forward_eos_to_zmq(event)
```

---

## 4. Deadlock prevention: GPU persistence mode

SV-PRO kế thừa đúng nguyên nhân deadlock từ `vms-savant`:

- host lock screen → GPU driver bị suspend
- GStreamer pipeline mất GPU context
- EOS storm → ZMQ queue tràn
- AI Core treo “Cannot accomplish in current state”

Giải pháp bắt buộc:

```ini
# /etc/systemd/system/nvidia-persistenced.service.d/override.conf
[Service]
ExecStart=
ExecStart=/usr/bin/nvidia-persistenced --user nvidia-persistenced --verbose
```

Và enable/start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nvidia-persistenced
nvidia-smi --query-gpu=persistence_mode --format=csv,noheader
```

Defense in depth:

- tắt screensaver / idle-delay
- mask suspend/hibernate targets (nếu phù hợp với môi trường deployment)

---

## 5. Health-check Watchdog & restart strategy

### 5.1 Mô hình “chờ health rồi restart”

Giống cách `vmsPro` có:

- `WaitUntilFrigateIsReadyAsync()`: đợi `/api/version` + sleep buffer
- `WaitUntilSavantIsReadyAsync()`: đợi `/status` + delay

SV-PRO nên áp dụng tương tự cho:

- restart ingress container khi ZMQ deadlock/EOS storm
- restart ai core khi metadata egress stop tăng
- restart JSON/video egress khi pipeline bị lỗi codec/disk full

### 5.2 Rule đề xuất

- restart ingress trước (AI core vẫn giữ GPU)
- nếu JSON không tăng trong X phút:
  - restart json egress
  - nếu vẫn không tăng: restart ai core
- luôn có “backoff + hạn chế restart loop” (ví dụ tối đa N lần / 10 phút)

---

## 6. Observability: log & metrics để phát hiện sớm

Bạn nên đảm bảo có thể quan sát được các tín hiệu:

- `ZMQ queue depth` / backlog theo camera/source_id
- FPS thật mỗi camera (ingress)
- inference latency theo stage/model
- count OCR success / skip / low confidence (LPR)
- JSON egress write rate (events/sec)

Trace:

- propagate `trace_id` từ ingress vào AI Core và attach vào JSON metadata
- trong log egress/ai core, luôn in `trace_id` để truy vết một frame/event

---

## 7. Troubleshooting runbook (symptom → action)

### 7.1 Symptom: “Pipeline running nhưng JSON không tăng”

Action:

1. kiểm tra container `savant-json-egress` có crash loop không
2. kiểm tra ZMQ PUB/SUB subscribers có reconnect không
3. nếu queue depth tăng và không giảm:
   - restart json egress
   - nếu không cải thiện: restart ai core
4. nếu có host lock/suspend trùng thời điểm:
   - xác nhận persistence mode đang `Enabled`
   - kiểm tra EOS storm logs

### 7.2 Symptom: “AI Core treo Cannot accomplish in current state”

Action:

1. xác nhận OS có lock/suspend
2. bật lại/kiểm tra persistence mode
3. áp EOS storm guard + HWM trên ingress
4. restart ai core (có backoff tránh crash loop)

---

Tham khảo runbook chi tiết với bước kiểm tra/restart theo thứ tự:
[`docs/Runbook_Reliability_Restart.md`](docs/Runbook_Reliability_Restart.md)

Contract metrics/drop reason codes để thống nhất quan sát và tổng hợp drop:
[`docs/Telemetry_Metrics_DropReason_Contract.md`](docs/Telemetry_Metrics_DropReason_Contract.md)

