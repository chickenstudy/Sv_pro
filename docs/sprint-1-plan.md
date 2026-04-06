# Kế hoạch Chi tiết Sprint 1: Hạ tầng & Thiết kế rập khuôn

Dựa trên cấu trúc dự án và yêu cầu từ người dùng, dưới đây là kế hoạch chi tiết triển khai **Sprint 1** được điều chỉnh cho môi trường hiện tại.

## Thống nhất & Nhận định (Socratic Gate Decisions)
1. **Môi trường Host:** Đang ở trên **Windows** (kết hợp WSL2/Docker Desktop). Task 1.3 (`nvidia-persistenced`) là chuẩn cho Linux, do đó trên hệ điều hành Windows chúng ta sẽ tạm bỏ qua lệnh `sudo systemctl` và chỉ cấu hình Docker Desktop hỗ trợ GPU Passthrough/WSL2.
2. **Nguồn Video:** Sử dụng **Dummy Stream (videotestsrc)** giả lập camera để kiểm tra pipeline Ingress -> AI Core -> Egress.
3. **Cảnh báo Telegram:** Chưa có Token, sẽ sử dụng **Placeholder** trong file `.env` (ví dụ: `TELEGRAM_BOT_TOKEN=your_token_here`).

---

## Danh sách Task Thực thi (Implementation Phase)

### 1. Chuẩn bị Môi trường Máy chủ (Windows/WSL Docker)
- **Task 1.3 (Adjusted)**: Tạo file `README_WINDOWS_DEV.md` hoặc thêm section ghi chú về cách enable GPU trên Docker Desktop (WSL2), bỏ qua service persistence mode do không khả dụng và không gây deadlock hệ điều hành như trên Ubuntu.

### 2. Thiết lập Hạ tầng Docker & ZeroMQ
- **Task 1.4**: Cấu hình file `docker-compose.yml` gốc cho hệ thống, thiết lập các service: `savant-video-ingress`, `savant-ai-core`, `savant-json-egress`, `postgres`, `redis`, `prometheus`, `grafana`.
- **Task 1.5 + 1.6**: Tạo cấu hình ZeroMQ IPC (`/tmp/sv-ipc/`) và cấu hình ngay High-Water Mark (`sndwm=100`, `rcvhwm=100`, `sndtimeo=200ms`) trong các file module Savant Ingress/Egress.
- **Task 1.8**: Bổ sung `healthcheck` cho các service trong `docker-compose.yml`.

### 3. Cải tiến Pipeline Savant (Reliability)
- **Task 1.7 + 1.10 + 1.12**: Cập nhật mã nguồn Python/Cấu hình của Ingress (`src/ingress/...` hoặc cấu hình ingest) để hỗ trợ EOS Storm Guard, Drop policy khi buffer đẩy, và cơ chế flush queue. *(Ghi chú: Savant cung cấp sẵn các tham số tùy chỉnh trong Ingress module, ta sẽ cấu hình qua biến môi trường).*
- **Task 1.9**: Đảm bảo pipeline emit metrics `queue depth` ra endpoint Prometheus.

### 4. Database & Models
- **Task 1.13**: Viết script `scripts/init_db.py` và file schema SQL (`scripts/sql/schema.sql`) để khởi tạo PostgreSQL với extension `pgvector` và tạo các bảng `users`, `recognition_logs`, v.v.
- **Task 1.14**: Tạo file `scripts/download_models.py` (sử dụng huggingface_hub hoặc requests) để tải SCRFD, ArcFace, YOLOv8s/n, PaddleOCR, MiniFASNet.

---

## Ký duyệt Kế hoạch

Vui lòng Review kế hoạch này. Khi bạn Xác nhận (Y), tôi sẽ triệu hồi đồng thời các Agent (`devops-engineer`, `backend-specialist`, `database-architect`) để bắt đầu code các file cấu hình và kịch bản cơ sở.
