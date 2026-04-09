# SV-PRO — Hướng dẫn cài đặt trên Linux

> **Ngôn ngữ:** Tiếng Việt
> **Phiên bản:** 1.0.0
> **Yêu cầu:** Ubuntu 20.04+ / Debian 11+, NVIDIA GPU
> **Models:** Đã bao gồm `yolov8n_plate` (fine-tuned VN)

---

## Mục lục

1. [Yêu cầu hệ thống](#1-yêu-cầu-hệ-thống)
2. [Cài đặt nhanh (Quick Setup)](#2-cài-đặt-nhanh-quick-setup)
3. [Giải thích từng bước](#3-giải-thích-từng-bước)
4. [Mô hình hoạt động](#4-mô-hình-hoạt-động)
5. [Sử dụng yolov8n_plate](#5-sử-dụng-yolov8n_plate)
6. [Tổng hợp lỗi thường gặp](#6-tổng-hợp-lỗi-thường-gặp)
7. [Cấu hình nâng cao](#7-cấu-hình-nâng-cao)
8. [Backup & Restore](#8-backup--restore)

---

## 1. Yêu cầu hệ thống

### Phần cứng tối thiểu

| Thành phần | Tối thiểu | Khuyến nghị |
|-----------|-----------|------------|
| GPU | NVIDIA GPU 4GB VRAM | RTX 3060 / RTX 4070 (8-12GB) |
| RAM | 8 GB | 16 GB |
| Ổ đĩa | 30 GB | 50 GB SSD NVMe |
| CPU | 4 cores | 8+ cores |

### Phần mềm cần thiết

| Thành phần | Phiên bản |
|-----------|-----------|
| Ubuntu / Debian | 20.04+ / 11+ |
| NVIDIA Driver | 525+ |
| Docker | 20.10+ |
| Docker Compose | 2.0+ |
| NVIDIA Container Toolkit | Latest |

### Kiểm tra nhanh

```bash
# 1. Kiểm tra GPU
nvidia-smi
# Kết quả mong đợi: bảng thông tin GPU hiện lên, không có lỗi

# 2. Kiểm tra CUDA
nvcc --version
# Kết quả mong đợi: NVCC version 11.x hoặc 12.x

# 3. Kiểm tra Docker
docker --version
docker compose version

# 4. Kiểm tra NVIDIA Docker
docker run --rm --gpus all nvidia/cuda:12.1.0-base nvidia-smi
```

---

## 2. Cài đặt nhanh (Quick Setup)

### Cách 1: Đóng gói trên Windows (PowerShell) — rồi copy tay

> **Dùng khi máy dev là Windows.** Script `package.ps1` build React + copy tất cả vào `deploy/`. Sau đó copy tay thư mục lên server.

```powershell
# 1. Trên máy Windows: đóng gói
cd Sv_pro\deploy
.\package.ps1

# 2. Copy thư mục deploy/ lên server (dùng rsync hoặc shared folder)
#    Ví dụ qua rsync (cài rsync trên Windows: choco install rsync):
rsync -avP deploy/ user@your-server:/tmp/svpro/

#    Hoặc dùng MobaXterm / WinSCP kéo-thả thủ công:
#    Copy toàn bộ thư mục deploy/ vào server

# 3. SSH vào server, sắp xếp
ssh user@your-server
sudo mkdir -p /opt/svpro
sudo cp -r /tmp/svpro/* /opt/svpro/
cd /opt/svpro

# 4. Sửa .env (bảo mật)
sudo nano .env
# Thay đổi: JWT_SECRET, ADMIN_PASSWORD, POSTGRES_PASSWORD

# 5. Cài đặt
chmod +x install.sh scripts/*.sh
sudo ./install.sh

# 6. Theo dõi logs
sudo docker compose logs -f
```

### Cách 2: Đóng gói trên Linux/Mac (Bash) — rồi scp

> **Dùng khi máy dev là Linux/Mac.** Script `package.sh` build React + đóng gói `.tar.gz`, tự động hướng dẫn SCP.

```bash
# 1. Trên máy Linux/Mac: đóng gói
cd Sv_pro/deploy
chmod +x package.sh
./package.sh
# → Output: svpro-deploy-YYYYMMDD.tar.gz

# 2. Upload lên server
scp svpro-deploy-YYYYMMDD.tar.gz user@your-server:/tmp/

# 3. SSH vào server, giải nén
ssh user@your-server
sudo mkdir -p /opt/svpro
sudo tar -xzvf /tmp/svpro-deploy-YYYYMMDD.tar.gz -C /opt/svpro
cd /opt/svpro

# 4. Sửa .env (bảo mật)
sudo nano .env
# Thay đổi: JWT_SECRET, ADMIN_PASSWORD, POSTGRES_PASSWORD

# 5. Cài đặt
chmod +x install.sh scripts/*.sh
sudo ./install.sh

# 6. Theo dõi logs
sudo docker compose logs -f
```

> ⚠️ **Lưu ý:** `savant-ai-core` (AI pipeline GPU) chỉ chạy được trên **Linux native** (Ubuntu 20.04+). Không chạy được trên Docker Desktop / WSL2 của Windows.

### Cách 3: Cài đặt thủ công

```bash
# Bước 1: Cài Docker + NVIDIA runtime
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable docker
sudo usermod -aG docker $USER

# Bước 2: Cài NVIDIA Container Toolkit
distribution=$(. /etc/os-release && echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
    sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -A "$distribution" \
    https://nvidia.github.io/libnvidia-container/container.deb.list | \
    sudo sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Bước 3: Copy source code
sudo mkdir -p /opt/svpro
sudo cp -r . /opt/svpro/

# Bước 4: Đặt models
# (Xem hướng dẫn phần Models bên dưới)

# Bước 5: Sửa .env
sudo nano /opt/svpro/.env
# Thay đổi: JWT_SECRET, ADMIN_PASSWORD, POSTGRES_PASSWORD

# Bước 6: Khởi động
cd /opt/svpro
sudo docker compose up -d --build

# Bước 7: Kiểm tra
./scripts/quickstart.sh
```

---

## 3. Giải thích từng bước

### Bước 1: Chuẩn bị Models

Dự án đi kèm **6 file model AI**:

```
models/
├── scrfd_10g_bnkps.onnx         # Nhận diện khuôn mặt (170MB)
├── glintr100.onnx                # Embedding khuôn mặt (90MB)
├── anti_spoof/minifasnet.onnx    # Chống giả mạo (5MB)
├── yolov8/yolov8s.onnx           # Phát hiện xe (50MB)
├── yolov8/yolov8s_config_savant.txt
└── yolov8n_plate/yolov8n_plate.onnx   # Phát hiện biển số VN (12MB) ✅ ĐÃ CÓ
```

> **Model `yolov8n_plate` đã được fine-tune cho biển số Việt Nam** — đi kèm trong bộ cài, không cần tải thêm.

**Models còn thiếu (copy từ máy dev nếu chưa có):**

```bash
# Từ máy dev (đã chạy được dự án):
scp -r models/* user@server:/opt/svpro/models/
```

> **Lưu ý:** `yolov8n_plate` đã nằm trong `models/` của bộ cài. Chỉ cần copy nếu models khác bị thiếu.

### Bước 2: Cấu hình .env

```bash
# Sửa file cấu hình
sudo nano /opt/svpro/.env
```

Các giá trị **bắt buộc phải thay đổi**:

```bash
# ⚠️ THAY ĐỔI ĐÂY - Bảo mật!
JWT_SECRET=use-a-long-random-string-at-least-32-chars
ADMIN_PASSWORD=your_strong_password_here
POSTGRES_PASSWORD=your_db_password_here
```

### Bước 3: Khởi động

```bash
cd /opt/svpro

# Khởi động infrastructure trước (DB, Redis)
sudo docker compose up -d postgres redis

# Đợi 10 giây cho PostgreSQL sẵn sàng
sleep 10

# Khởi động toàn bộ
sudo docker compose up -d

# Kiểm tra
sudo docker compose ps
```

### Bước 4: Truy cập

| Dịch vụ | URL | Tài khoản |
|---------|-----|----------|
| **Dashboard** | http://localhost | admin / svpro2024 |
| **Backend API** | http://localhost:8000 | API Docs: /docs |
| **AI Core** | http://localhost:8080 | health check |
| **Prometheus** | http://localhost:9090 | - |
| **Grafana** | http://localhost:3001 | admin / svpro2024 |
| **PostgreSQL** | localhost:5432 | svpro_user / svpro_pass |

---

## 4. Mô hình hoạt động

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Camera RTSP Stream                              │
│                    rtsp://192.168.1.100:554/live                       │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    INGRESS MANAGER                                       │
│  ┌─────────────┐   ┌────────────────┐   ┌──────────────────────────┐  │
│  │ PostgreSQL  │ → │ Poll cameras   │ → │ ZMQ publisher           │  │
│  │ (camera DB) │   │ every 10s      │   │ (input-video.ipc)      │  │
│  └─────────────┘   └────────────────┘   └──────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐
│   YOLOv8 Primary  │ │  Object Tracker  │ │    Paddle OCR     │
│  (Vehicle Detect) │ │   (NvSORT)       │ │  (Plate Detect)   │
└────────────────���──┘ └───────────────────┘ └───────────────────┘
        │                      │                      │
        └──────────────────────┼──────────────────────┘
                               ▼
┌────────────────────────────────��────────────────────────────────────────┐
│                   BLACKLIST ENGINE                                      │
│  ┌────────────┐   ┌─────────────┐   ┌─────────────────┐                 │
│  │ PostgreSQL │ ← │   Redis     │ ← │  AI Inference   │                 │
│  │ (blacklist)│   │ (L2 cache)  │   │   results       │                 │
│  └────────────┘   └─────────────┘   └─────────────────┘                 │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐
│  Alert Manager    │ │  REST API (FastAPI)│ │  JSON Egress     │
│  (Telegram)       │ │  /api/events     │ │  (metadata log)  │
└───────────────────┘ └───────────────────┘ └───────────────────┘
```

---

## 5. Sử dụng yolov8n_plate

### Giải thích

Dự án dùng **2 model YOLOv8**:

| Model | Kích thước | Mục đích | Config |
|-------|-----------|---------|--------|
| `yolov8s.onnx` | ~50MB | Phát hiện **xe** (car, truck, bus) | `module.yml` → `plate_ocr` |
| `yolov8n_plate.onnx` | ~15MB | Phát hiện **biển số** (nhẹ hơn 3x) | `module.yml` → `plate_ocr` |

### Nếu chưa có yolov8n_plate

**Tùy chọn A — Sử dụng model có sẵn:**
```bash
# Tải YOLOv8n chuẩn từ Ultralytics
python3 -c "from ultralytics import YOLO; m = YOLO('yolov8n.pt'); m.export(format='onnx')"
mv yolov8n.onnx models/yolov8n_plate/yolov8n_plate.onnx
```

**Tùy chọn B — Fine-tune cho biển số Việt Nam:**
```bash
# 1. Chuẩn bị dataset biển số VN (khoảng 500-1000 ảnh)
# 2. Annotate bằng Label Studio hoặc CVAT
# 3. Train
python3 -c "
from ultralytics import YOLO
m = YOLO('yolov8n.pt')
m.train(data='vietnam_plate.yaml', epochs=100, imgsz=640)
m.export(format='onnx')
"
# 4. Đặt vào
mv runs/detect/train/weights/best.onnx models/yolov8n_plate/yolov8n_plate.onnx
```

**Tùy chọn C — Dùng tạm yolov8s_plate (lớn hơn):**
```bash
# Chỉnh sửa module.yml — thay đổi model path:
# plate_model_path: /models/yolov8s_plate/yolov8s_plate.onnx

# Hoặc chỉnh sửa .env:
PLATE_MODEL_PATH=/models/yolov8s_plate/yolov8s_plate.onnx
```

### Kiểm tra model đã load đúng

```bash
# Xem logs AI Core
sudo docker compose logs -f savant-ai-core | grep -i "plate"

# Kết quả mong đợi:
# [INFO] Using plate model: /models/yolov8n_plate/yolov8n_plate.onnx
```

---

## 6. Tổng hợp lỗi thường gặp

### Lỗi 1: GPU không nhận được trong container

**Triệu chứng:**
```
docker: Error response from daemon: could not select device driver "nvidia"...
```

**Khắc phục:**
```bash
# 1. Cài NVIDIA Container Toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 2. Kiểm tra
docker run --rm --gpus all nvidia/cuda:12.1.0-base nvidia-smi

# 3. Nếu vẫn lỗi — thử cài driver
sudo add-apt-repository ppa:graphics-drivers/ppa
sudo apt install -y nvidia-driver-535
sudo reboot
```

---

### Lỗi 2: Backend 500 — strangers endpoint

**Triệu chứng:**
```
asyncpg.exceptions.IndeterminateDatatypeError: could not determine data type of parameter $1
```

**Nguyên nhân:** Bug trong LIMIT/OFFSET interpolation.

**Khắc phục:** Đã fix trong code mới nhất. Rebuild:

```bash
cd /opt/svpro
sudo docker compose build backend
sudo docker compose up -d backend
```

---

### Lỗi 3: AI Core crash liên tục (Segmentation fault)

**Triệu chứng:**
```
Segmentation fault — Paddle OCR
```

**Nguyên nhân:** Paddle OCR nhạy cảm với GPU driver trên Windows Docker Desktop.
Trên Linux thuần, lỗi này **hiếm khi xảy ra**.

**Khắc phục:**
```bash
# Khởi động lại AI Core
sudo docker compose restart savant-ai-core

# Nếu crash liên tục, xem logs chi tiết:
sudo docker compose logs -f savant-ai-core --tail=50

# Kiểm tra GPU:
nvidia-smi
```

---

### Lỗi 4: Camera không hiện trên dashboard

**Triệu chứng:**
```
No source available for cam_online_1
```

**Khắc phục:**
```bash
# 1. Kiểm tra camera trong DB:
sudo docker exec svpro_postgres psql -U svpro_user -d svpro_db -c \
    "SELECT id, name, rtsp_url FROM cameras WHERE enabled = true;"

# 2. Kiểm tra RTSP URL có đúng format:
# Đúng:  rtsp://admin:password@192.168.1.100:554/stream
# Sai:   rtsp://192.168.1.100/stream (thiếu authentication)

# 3. Test camera stream trên server:
ffmpeg -i "rtsp://admin:password@192.168.1.100:554/live" \
    -t 5 -f null - 2>&1 | tail -5
```

---

### Lỗi 5: Database migrations lỗi

**Triệu chứng:**
```
psql: could not connect to server: Connection refused
```

**Khắc phục:**
```bash
# Đợi PostgreSQL khởi động xong
sudo docker compose up -d postgres
sleep 15

# Kiểm tra healthy
sudo docker compose ps postgres

# Chạy lại migrations thủ công:
sudo docker compose up db-init
```

---

### Lỗi 6: Ingress Manager không gửi video

**Triệu chứng:**
```
No source available
```

**Khắc phục:**
```bash
# Xem logs
sudo docker compose logs ingress-manager | tail -20

# Kiểm tra camera trong DB
sudo docker exec svpro_postgres psql -U svpro_user -d svpro_db -c \
    "SELECT name, rtsp_url, enabled FROM cameras;"

# Kiểm tra polling interval
# Mặc định: 10 giây — camera xuất hiện sau tối đa 10s
```

---

## 7. Cấu hình nâng cao

### 7.1 Đổi cổng Dashboard

```bash
# Sửa docker-compose.yml:
#   ports:
#     - "8080:8080"      # AI Core
#     - "8000:8000"       # Backend
#     - "9090:9090"       # Prometheus
#     - "3001:3001"       # Grafana
#     → Đổi 3001 thành cổng khác: "8081:3000"
```

### 7.2 Cấu hình Telegram Alerts

```bash
# Sửa .env:
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=-1001234567890
ALERT_WEBHOOK_URL=

# Tạo bot Telegram: @BotFather → /newbot
# Lấy chat_id: gửi message trong group rồi dùng:
# curl https://api.telegram.org/bot<TOKEN>/getUpdates
```

### 7.3 Tăng/giảm AI processing FPS

```bash
# Sửa module.yml:
pipeline:
  batch_size: 1          # Giảm nếu GPU yếu
  batched_push_timeout: 2000  # ms

# Sửa camera trong DB (per-camera):
UPDATE cameras SET fps_limit = 10 WHERE name = 'cam_online_1';
```

### 7.4 Thêm camera mới

```bash
# Qua API (admin):
curl -X POST http://localhost:8000/api/cameras \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cam_entrance_2",
    "rtsp_url": "rtsp://admin:password@192.168.1.101:554/stream",
    "location": "Cổng sau",
    "zone": "entrance",
    "ai_mode": "both",
    "fps_limit": 15
  }'
```

### 7.5 Chạy không có GPU (CPU mode — chậm)

```bash
# Sửa docker-compose.yml — bỏ GPU reservation:
savant-ai-core:
  # runtime: nvidia     # comment dòng này
  deploy:
    resources:
      reservations:
        devices: []    # bỏ nvidia device

# ⚠️ Chế độ CPU rất chậm, chỉ dùng để test
```

---

## 8. Backup & Restore

### Backup Database

```bash
# Tự động mỗi ngày (thêm vào crontab)
sudo crontab -e
# Thêm dòng:
0 2 * * * docker exec svpro_postgres pg_dump -U svpro_user svpro_db > /backup/svpro_$(date +\%Y\%m\%d).sql
```

### Backup toàn bộ data

```bash
# Backup volume data
sudo docker run --rm -v svpro_pgdata:/data -v /backup:/backup alpine \
    tar czf /backup/svpro_volumes_$(date +\%Y\%m\%d).tar.gz -C /data .

# Backup models
tar czf /backup/svpro_models.tar.gz models/
```

### Restore

```bash
# Restore DB
cat /backup/svpro_latest.sql | docker exec -i svpro_postgres psql -U svpro_user svpro_db

# Restore volumes
docker stop svpro_postgres svpro_redis
docker volume rm svpro_pgdata svpro_redis_data
# (Volume sẽ tự tạo lại khi start)
docker start svpro_postgres svpro_redis
```

---

## Lệnh tổng hợp

```bash
# Di chuyển vào thư mục deploy
cd /opt/svpro

# Xem trạng thái tất cả services
sudo docker compose ps

# Xem logs một service
sudo docker compose logs -f savant-ai-core

# Restart một service
sudo docker compose restart backend

# Restart toàn bộ
sudo docker compose restart

# Dừng tất cả
sudo docker compose down

# Dừng + xóa data (⚠️  XÓA HẾT DATA!)
sudo docker compose down -v

# Rebuild sau khi sửa code
sudo docker compose build --parallel
sudo docker compose up -d

# Kiểm tra nhanh
bash scripts/quickstart.sh
```

---

## Liên hệ & Hỗ trợ

| Kênh | Thông tin |
|------|----------|
| Logs | `sudo docker compose logs -f` |
| Health | `http://localhost:8000/health` |
| API Docs | `http://localhost:8000/docs` |
| Prometheus | `http://localhost:9090/graph` |
| Grafana | `http://localhost:3001` |

**Tài liệu thêm:**
- Savant Framework: https://docs.savant-ai.io
- FastAPI: https://fastapi.tiangolo.com
- DeepStream: https://docs.nvidia.com/metropolis/deepstream/dev-guide/
