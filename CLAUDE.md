# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

SV-PRO (Savant-Vision Professional) is a real-time video analytics platform built on **NVIDIA DeepStream 7.x + Savant 0.6.x**. It runs a **Dual-AI pipeline** on RTSP camera streams: License Plate Recognition (LPR) and Face Recognition (FR), sharing a single primary YOLOv8s detector on the GPU then branching into independent `pyfunc` stages.

The system is split into ~8 Docker services orchestrated by `docker-compose.yml`. Communication between Ingress ↔ AI Core uses ZeroMQ over Unix-domain sockets (`/tmp/zmq-sockets/*.ipc`) rather than network sockets — this is intentional for zero-copy frame transport.

## Commonly used commands

### Stack lifecycle (Docker)
```bash
docker compose up -d --build          # Build & start everything
docker compose down                   # Stop
docker compose logs -f savant-ai-core # Tail AI pipeline logs
docker compose logs -f savant-rtsp-ingress
docker compose restart savant-ai-core # Restart only AI Core (other services keep running)
```

### Tests (pytest)
```bash
pip install -r requirements-dev.txt
pytest                                  # All tests, verbose, strict markers
pytest tests/unit/lpr                   # One module
pytest tests/unit/lpr/test_normalize_plate.py::TestNormalize::test_x  # Single test
pytest -m "not slow and not gpu"        # Skip slow/GPU-only
pytest -m integration                   # Integration tests only
```
`pytest.ini` defines markers `slow`, `gpu`, `integration` and enables `asyncio_mode = auto`.

### Dashboard (React + Vite)
```bash
cd dashboard
npm run dev      # Vite dev server
npm run build    # tsc + vite build → dashboard/dist
npm run lint     # eslint src --ext ts,tsx
```

### One-shot setup wizard
```bash
python scripts/setup.py             # Interactive wizard: prereq check → .env → models → docker compose up
python scripts/setup.py --no-docker # Configure only, skip docker
python scripts/download_models.py   # Pull ONNX model files into ./models/
```

### Database
```bash
docker compose exec postgres psql -U svpro_user -d svpro_db -c "\dt"
# Migrations under scripts/sql/migrations/ (001..004) are run automatically by db-init service
# To re-run by hand:
docker exec -it svpro_postgres psql -U svpro_user -d svpro_db -f /sql/schema.sql
```

## Architecture — what requires reading multiple files to understand

### Two Dockerfiles, one shared image base
- `Dockerfile.savant-ai-core` is built from `ghcr.io/insight-platform/savant-deepstream` and contains `savant_rs` (Rust bindings for VideoFrame protobuf serialization). Both `savant-ai-core` AND `savant-rtsp-ingress` services build from this Dockerfile — *not* `Dockerfile.backend`. `rtsp_ingest.py` will fail to serialize frames if run from the backend image because `savant_rs` is missing there. Docker layer cache is shared, so this doesn't double disk usage.
- `Dockerfile.backend` (python:3.11-slim) is for the FastAPI backend AND the lightweight `go2rtc-sync` poller. The `entrypoint` is overridden in `docker-compose.yml` for `go2rtc-sync` to run a different module.

### Dataflow & service boundaries
```
IP camera (RTSP)
  → svpro-go2rtc          (RTSP broker; HTTP API on 1985, RTSP re-stream on 8556, WebRTC on 8557)
  → go2rtc-sync           (listens to PG NOTIFY cameras_changed → PUTs streams to go2rtc REST API)
  → savant-rtsp-ingress   (cv2.VideoCapture → savant_rs VideoFrame → ZMQ PUB on input-video.ipc)
  → savant-ai-core        (DeepStream pipeline defined by module/module.yml)
       ├─ Stage 1  nvinfer YOLOv8s (TensorRT FP16)  — detect person/car/motorcycle/bus/truck
       ├─ Stage 1b nvtracker NvSORT                 — assigns track_id used by FR/LPR caches
       ├─ Stage 2a pyfunc src.lpr.plate_ocr.PlateOCR        (vehicle crops)
       ├─ Stage 2b pyfunc src.fr.face_recognizer.FaceRecognizer (person crops)
       └─ Stage 3  pyfunc src.business.blacklist_engine.BlacklistPyfunc
                            └─ alert_manager → POST /api/events/ingest (Telegram/Webhook)
                            └─ audit_logger  → psycopg2 → events table
  → backend (FastAPI on :8000)  — CRUD cameras/users/vehicles/events; serves /metrics for Prometheus
  → frontend (Nginx on :8081)   — React SPA built from dashboard/
  → stranger-dedup              — hourly cron; union-find merge duplicate stranger_id (cosine ≥ 0.55)
```

There is **no `json-egress` and no `video-egress`** — the AI Core writes events directly to Postgres + alert webhook. `output/` exists historically but is not consumed.

### Where each pipeline stage lives
Pipeline shape is declared in `module/module.yml`. Each `pyfunc` line points to a Python class:
- `src/converters/yolov8_converter.py` — DeepStream tensor → bbox post-processing for YOLOv8
- `src/lpr/plate_ocr.py` — YOLOv8n plate detector + PaddleOCR v4 + temporal voting (`vote_window: 8`) + night-mode CLAHE
- `src/fr/face_recognizer.py` — YOLOv8-face → ArcFace R100 → MiniFASNet anti-spoof; 2-tier cache (L1 in-process LRU, L2 Redis), pgvector cosine match + stranger gallery multi-embedding (K=5)
- `src/fr/yolov8_face.py` — YOLOv8n-face ONNX wrapper (thay SCRFD Apr 2026 — ít false-positive trên non-face; 5-pt landmarks chuẩn insightface)
- `src/fr/face_align.py` — module chung `align_face()` + `ARCFACE_DST` template. CRITICAL: cả runtime (FaceRecognizer) lẫn `enrollment_service` đều dùng hàm này — nếu không, embedding enroll vs runtime sẽ lệch → người quen bị nhận stranger
- `src/fr/stranger_reid.py`, `src/fr/face_quality.py`, `src/fr/enrollment_service.py` — sub-modules used by FaceRecognizer (enrollment service runs an HTTP server in a thread inside the AI Core, port 8090, exposes `/internal/enroll` + `/internal/reload-embeddings`. Backend gọi reload sau mỗi enroll → L1 cache cleared + Redis staff hash re-prefetched NGAY)
- `src/business/blacklist_engine.py` — last stage, owns `ObjectLinker` + `AlertManager` + `AuditLogger` instances
- `src/lpr/roi_eval*.py` — separate cron-style ROI autopilot (analyzes `/Detect/` heatmap and rewrites `module.yml` if `AUTO_APPLY_ROI=true`)

### Configuration & camera/zone state
- `module/module.yml` is the canonical pipeline config. Per-camera ROI zones (`roi_zones`) and `camera_zones` start empty `{}` — they are loaded from the Postgres `cameras` table at runtime, not from YAML. Editing the YAML for these maps is generally wrong; update the DB and let PG NOTIFY propagate.
- `config/go2rtc.yaml` is the go2rtc broker config. Streams are added/removed dynamically by `src/ingress/go2rtc_sync.py` listening on `LISTEN cameras_changed` (trigger from migration `002_camera_notify_trigger.sql`).
- `tracker/config_tracker_NvSORT.yml` tunes the DeepStream NvSORT tracker.

### Backend layout
`backend/main.py` mounts routers under `/api/*`: `auth, cameras, users, vehicles, events, doors, strangers, metrics, stream, enroll, images`. `enroll.py` proxies to `AI_CORE_ENROLL_URL` (the in-process HTTP server inside `savant-ai-core`). `database.py` uses `asyncpg` with `init_db()` called from a lifespan hook.

### Output artifact convention
Successful detections are saved as image crops + JSON sidecar to `./Detect/{camera_id}/{date}/{category}/`. The schema is documented in `README.md` (Output Metadata JSON section). The `Detect/` volume is bind-mounted into `savant-ai-core`.

## Project-specific gotchas

- **NVIDIA Persistence Mode is mandatory on Linux hosts** (`sudo systemctl enable nvidia-persistenced`). Without it, the GPU pipeline deadlocks when the screen locks/sleeps — symptoms are ZMQ queue overflow and GStreamer `Cannot accomplish in current state`. See `docs/Architecture.md#troubleshooting`.
- **First container start is slow (5–10 min)** while TensorRT compiles ONNX → `.engine` for the host GPU. Engines are cached at `models/engine/`. Don't kill the container during this.
- **Do not install pip `opencv-python*` into the AI Core image.** It shadows the DeepStream-bundled `cv2` and breaks features like `cv2.cuda.ALPHA_OVER`. `Dockerfile.savant-ai-core` explicitly uninstalls it after PaddleOCR pulls it in.
- **Numpy is pinned to `<2`** because Savant 0.6.x and several models require it. Do not upgrade.
- **`output_frame.codec` in `module.yml` is currently `raw-rgb24`** (WSL2-friendly). Switch to `codec: h264, encoder: nvenc` for Linux production deployments.
- **Port mapping deliberately offsets from a co-deployed VMS instance**: postgres host port 5433 (not 5432), go2rtc HTTP 1985 (not 1984), AI Core 9080 (not 8080), grafana 3001 (not 3000). Don't "fix" these to defaults without checking.
- The `.venv/` directory inside the repo root is ignored by git but is what local (non-Docker) development uses against `requirements-dev.txt`.
