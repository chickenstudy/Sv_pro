# GEMINI.md - SV-PRO (Savant-Vision Professional)

> **Hệ thống phân tích Video thời gian thực (Real-time Video Analytics)**  
> Xây dựng trên nền tảng **NVIDIA DeepStream 7.x** + **Savant Framework**.

---

## 📋 Project Overview

SV-PRO là một hệ thống giám sát thông minh triển khai **Dual-AI Pipeline** chạy song song trên GPU, cho phép đồng thời:
1.  **Nhận diện Biển số xe (LPR):** Sử dụng YOLOv8n + PaddleOCR v4, hỗ trợ biển số Việt Nam (biển 2 dòng).
2.  **Nhận diện Khuôn mặt (FR):** Sử dụng SCRFD + ArcFace R100 + Anti-Spoofing (MiniFASNet).

Hệ thống sử dụng kiến trúc phân tán qua **ZeroMQ (IPC)**, cho phép mở rộng và khởi động lại các thành phần (Ingress, AI Core, Backend) một cách độc lập mà không làm gián đoạn luồng xử lý GPU.

### 🛠 Technology Stack

-   **AI Core:** Savant (Python/C++ wrapper for DeepStream), TensorRT FP16 models.
-   **Backend:** FastAPI (Python 3.10+), SQLAlchemy/AsyncPG.
-   **Frontend:** React (TypeScript), Vite, HLS.js for live streaming.
-   **Database:** PostgreSQL 16 + **pgvector** (HNSW index for face embeddings).
-   **Caching:** Redis 7 (Hot-cache cho nhân viên thường trú).
-   **Infrastructure:** Docker Compose, go2rtc (RTSP Broker), ZeroMQ, Prometheus, Grafana.

---

## 🚀 Building and Running

### Prerequisites
-   **GPU:** NVIDIA RTX 3060+ (12GB VRAM khuyến nghị).
-   **Driver:** NVIDIA Driver ≥ 525 + NVIDIA Container Toolkit.
-   **Persistence Mode:** `sudo systemctl enable --now nvidia-persistenced` (Bắt buộc).

### 1. Download AI Models
Tải các model ONNX cần thiết cho pipeline:
```bash
python scripts/download_models.py
```

### 2. Full Stack Start (Docker)
Khởi động toàn bộ dịch vụ (Postgres, Redis, Backend, Frontend, AI Core):
```bash
docker compose up -d --build
```

### 3. Local Development
Nếu bạn muốn chạy từng phần độc lập để debug:

**Backend API:**
```bash
cd backend
pip install -r ../requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

**Dashboard:**
```bash
cd dashboard
npm install
npm run dev
```

**AI Core (Savant):**
Sử dụng Savant Dev Container hoặc chạy qua Docker:
```bash
docker compose up savant-ai-core -d
```

---

## 📂 Architecture & Directory Structure

-   `backend/`: FastAPI application (routers, database logic).
-   `dashboard/`: React TypeScript frontend.
-   `src/`: Core AI logic & plugins for Savant.
    -   `fr/`: Face Recognition pipeline (SCRFD, ArcFace).
    -   `lpr/`: License Plate Recognition (YOLOv8n, PaddleOCR).
    -   `ingress/`: RTSP ingestion & go2rtc synchronization.
    -   `business/`: Blacklist engine & object linking logic.
-   `module/`: Savant configuration (`module.yml`).
-   `models/`: AI model storage (ONNX/TensorRT).
-   `docs/`: Extensive technical documentation (Architecture, Database, etc.).
-   `.agent/`: Antigravity Kit (Specialist agents, skills, and validation scripts).

---

## 🛠 Development Conventions

### 1. Agent-First Workflow
Dự án tích hợp **Antigravity Kit**. Trước khi thực hiện thay đổi lớn, hãy kích hoạt agent phù hợp:
-   `frontend-specialist` cho Dashboard.
-   `backend-specialist` cho API/Database.
-   `debugger` khi xử lý lỗi pipeline.

### 2. Validation & Testing
Mọi thay đổi code phải được kiểm tra qua bộ script master:
-   `python .agent/scripts/checklist.py .` (Quick check: Lint, Security, Tests).
-   `python .agent/scripts/verify_all.py .` (Full audit trước khi deploy).

### 3. Coding Style
-   **Python:** Tuân thủ PEP8, sử dụng Type Hints. Clean Code patterns (`@[skills/clean-code]`).
-   **TypeScript:** Strict typing, Functional Components (React).
-   **SQL:** Sử dụng migrations (`scripts/sql/migrations/`). Không sửa schema trực tiếp.

### 4. GPU Management
-   Tránh leak VRAM bằng cách không khởi tạo model thủ công trong vòng lặp.
-   Sử dụng `pyfunc` của Savant để tích hợp logic Python vào pipeline GStreamer.

---

## 🔗 Quick Links
-   **API Docs:** `http://localhost:8000/docs`
-   **Grafana:** `http://localhost:3001` (admin/svpro2024)
-   **go2rtc UI:** `http://localhost:1985`
-   **Project Roadmap:** `docs/Project_Plan.md`
