## Control Plane Orchestration (kế thừa tinh thần vmsPro)

Mục tiêu tài liệu này: giúp bạn thiết kế/viết tài liệu triển khai **Control plane** cho SV-PRO theo tinh thần `vmsPro`.

`vmsPro` (backend REST .NET 8) là “điều khiển hệ sinh thái”:

- register node (Go2RTC / Frigate / Savant)
- add/delete camera
- push config update async tới workers
- workers upsert YAML config và restart service khi cần

SV-PRO cần tương tự để:

- quản lý nhiều camera/source_id
- scale ingress instance (savant-video-ingress N instances)
- cập nhật `module/module.yml` (roi_zones, thresholds, enable_lpr/enable_fr, fps_limit…)
- giám sát health và restart có kiểm soát

---

## 1. Design goals cho control plane SV-PRO

- **Idempotent**: đăng ký/lệnh config nhiều lần vẫn cho trạng thái cuối cùng đúng.
- **Non-blocking**: endpoint trả nhanh, việc nặng giao cho background workers.
- **Bounded queues**: tránh queue vô hạn khi AI core/egress chậm.
- **Fault isolation**: restart ingress không làm gián đoạn AI core nếu không cần.

---

## 2. Data model đề xuất (mapping sang runtime)

### 2.1 Đối tượng chính

- `cameras`
  - `id`, `name`
  - `rtsp_url`
  - `source_id` (đúng với `SOURCE_ID` trong ingress adapter)
  - `ai_mode`: `dual | lpr | fr | off`
  - `roi_zones` (hoặc tham chiếu bản ROI theo camera)
  - `fps_limit`, `resolution`, `is_active`

- `ingress_instances`
  - `id`, `group_id`
  - `container_name` / `compose_service`
  - `cuda_device` / `ipc_path` (nếu sharding theo GPU)

- `ai_cores`
  - `id`, `group_id`
  - `container_name`
  - `ipc_input` / `ipc_output`

- `pipelines`
  - `camera_id`, `ai_core_id`, `module_version`
  - `status`: `running | degraded | offline`

### 2.2 Quy tắc map vào `module/module.yml`

Trong `module/module.yml`, bạn sẽ gán:

- `roi_zones` theo `source_id`
- thresholds cho LPR
- bật/tắt PyFunc blocks (nếu Savant cho phép toggle runtime)

---

## 3. API endpoints gợi ý (cấu trúc giống vmsPro)

Bạn có thể thiết kế base route `api/system` và `api/cameras` tương tự:

### 3.1 System

- `POST /api/system/register-ai-core`
  - body: `{ ip, api_port, zmq_ports, docker_daemon_port, max_camera_capacity, is_active }`

- `POST /api/system/register-ingress-group`
  - body: `{ group_name, cuda_device, ipc_input_path, ipc_output_path, max_camera_capacity }`

- `GET /api/system/ai-cores`
- `GET /api/system/ingress-groups`

### 3.2 Camera

- `POST /api/cameras`
  - body: `{ name, source_id, rtsp_url, ai_mode, fps_limit, resolution_w, resolution_h, roi_zones?, lpr_thresholds? }`

- `GET /api/cameras`
- `PUT /api/cameras/{id}/config`
  - thay `roi_zones`, thresholds, hoặc `ai_mode`

- `POST /api/cameras/{id}/start`
- `POST /api/cameras/{id}/stop`

- `DELETE /api/cameras/{id}`

--- 

## 4. Orchestration workflow (giống “async worker” của vmsPro)

### 4.1 Add camera

1. Control plane validate:
   - chọn `ingress group` + `ai core` phù hợp capacity
   - validate `roi_zones` format
2. Ghi DB trạng thái `pending`
3. Enqueue job:
   - start ingress adapter với `RTSP_URI` + `SOURCE_ID`
   - update runtime config (module.yml / environment)
4. Background worker:
   - wait health của ingress/ai-core (port/health endpoint)
   - restart hoặc reload config
5. DB trạng thái `running` (hoặc `degraded`)

### 4.2 Update config (ROI/threshold)

- atomic update `module.yml` (không ghi thẳng file sống)
- restart **chỉ** những component cần thiết (ví dụ restart ai-core để pyfunc đọc lại config)

### 4.3 Remove camera

- stop ingress adapter (hoặc remove container)
- chỉ định egress/json writer dừng ghi mới theo camera_id/source_id
- update module state nếu cần (tùy cách pyfunc đọc roi_zones)

---

## 5. Scaling/sharding cho multi-GPU

SV-PRO docs mô tả sharding IPC theo `CUDA_VISIBLE_DEVICES` và IPC path.

Control plane nên:

- tạo “group” theo GPU
- gán camera vào group
- đảm bảo mỗi `ai core` bind vào IPC path riêng để tránh xung đột socket

---

## 6. Implementation notes (khuyến nghị)

- Nếu bạn triển khai control plane trong .NET:
  - dùng `BackgroundService`
  - dùng `System.Threading.Channels` bounded với `DropOldest`
- Nếu triển khai Python:
  - dùng queue + worker threads/process với bound size

Điểm cốt lõi là:

- endpoint nhanh
- queue có giới hạn
- restart có backoff

