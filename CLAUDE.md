# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

SV-PRO (Savant-Vision Professional) is a real-time video analytics platform built on **NVIDIA DeepStream 7.x + Savant 0.6.x**. It runs a **Dual-AI pipeline** on RTSP camera streams: License Plate Recognition (LPR) and Face Recognition (FR), sharing a single primary YOLOv8s detector on the GPU then branching into independent `pyfunc` stages.

The system is split into ~8 Docker services orchestrated by `docker-compose.yml`. Communication between Ingress ↔ AI Core uses ZeroMQ over Unix-domain sockets (`/tmp/zmq-sockets/*.ipc`) rather than network sockets — intentional for zero-copy frame transport.

## Commonly used commands

### Stack lifecycle (Docker)
```bash
docker compose up -d --build          # Build & start everything
docker compose down                   # Stop
docker compose logs -f savant-ai-core # Tail AI pipeline logs
docker compose logs -f savant-rtsp-ingress
docker compose restart savant-ai-core # Restart only AI Core
```

### Tests (pytest)
```bash
pip install -r requirements-dev.txt
pytest                                  # All tests
pytest tests/unit/lpr                   # One module
pytest tests/unit/lpr/test_normalize_plate.py::TestNormalize::test_x  # Single test
pytest -m "not slow and not gpu"        # Skip slow/GPU-only
pytest -m integration                   # Integration tests only
```
`pytest.ini` defines markers `slow`, `gpu`, `integration` and enables `asyncio_mode = auto`.

### Dashboard (React + Vite)
```bash
cd dashboard
npm run dev      # Vite dev server on :5173
npm run build    # tsc + vite build → dashboard/dist
npm run lint     # eslint src --ext ts,tsx
```

**Dev login bypass**: Username `admin` / password `admin` skips the backend entirely — stores a sentinel token `__svpro_dev_admin_offline__` in localStorage. `authApi.me()` returns mock data for this token. All other API calls (cameras, events) still hit the real backend; they fail gracefully when it's down. This is implemented in `dashboard/src/api.ts` via `DEV_TOKEN` + `isDevMode()`.

### One-shot setup wizard
```bash
python scripts/setup.py             # Interactive: prereq check → .env → models → docker compose up
python scripts/setup.py --no-docker # Configure only, skip docker
python scripts/download_models.py   # Pull ONNX model files into ./models/
```

### Database
```bash
docker compose exec postgres psql -U svpro_user -d svpro_db -c "\dt"
# Migrations 001..007 under scripts/sql/migrations/ run automatically by db-init service
# To re-run by hand:
docker exec -it svpro_postgres psql -U svpro_user -d svpro_db -f /sql/schema.sql
```

## Architecture — what requires reading multiple files to understand

### Two Dockerfiles, one shared image base
- `Dockerfile.savant-ai-core` is built from `ghcr.io/insight-platform/savant-deepstream` and contains `savant_rs` (Rust bindings for VideoFrame protobuf serialization). Both `savant-ai-core` AND `savant-rtsp-ingress` services build from this Dockerfile — *not* `Dockerfile.backend`. `rtsp_ingest.py` will fail to serialize frames if run from the backend image because `savant_rs` is missing there.
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
                            └─ audit_logger  → psycopg2 → recognition_logs + access_events tables
  → backend (FastAPI on :8000)  — CRUD cameras/users/vehicles/events; serves /metrics for Prometheus
  → frontend (Nginx on :8081)   — React SPA built from dashboard/
  → stranger-dedup              — hourly cron; union-find merge duplicate stranger_id (cosine ≥ 0.55)
```

There is **no `json-egress` and no `video-egress`** — the AI Core writes events directly to Postgres + alert webhook.

### Where each pipeline stage lives
Pipeline shape is declared in `module/module.yml`. Each `pyfunc` line points to a Python class:
- `src/converters/yolov8_converter.py` — DeepStream tensor → bbox post-processing for YOLOv8
- `src/lpr/plate_ocr.py` — YOLOv8n plate detector + PaddleOCR v4 + temporal voting (`vote_window: 8`) + night-mode CLAHE
- `src/fr/face_recognizer.py` — YOLOv8-face → ArcFace R100 → MiniFASNet anti-spoof; 2-tier cache (L1 in-process LRU, L2 Redis), pgvector cosine match + stranger gallery multi-embedding (K=5)
- `src/fr/yolov8_face.py` — YOLOv8n-face ONNX wrapper (replaced SCRFD Apr 2026 — fewer false-positives on non-face; 5-pt landmarks compatible with insightface)
- `src/fr/face_align.py` — shared `align_face()` + `ARCFACE_DST` template. **CRITICAL**: both runtime (FaceRecognizer) and `enrollment_service` must use this same function — mismatched alignment causes known persons to be detected as strangers.
- `src/fr/stranger_reid.py`, `src/fr/face_quality.py`, `src/fr/enrollment_service.py` — sub-modules used by FaceRecognizer. Enrollment service runs an HTTP server in a thread inside the AI Core on port 8090, exposes `/internal/enroll` + `/internal/reload-embeddings`. Backend calls reload after each enroll → L1 cache cleared + Redis staff hash re-prefetched immediately.
- `src/business/blacklist_engine.py` — last stage, owns `ObjectLinker` + `AlertManager` + `AuditLogger` instances
- `src/lpr/roi_eval*.py` — cron-style ROI autopilot: analyzes `/Detect/` heatmap and rewrites `module.yml` if `AUTO_APPLY_ROI=true`

### Database schema — two event tables
Events come from two separate tables joined by a UNION CTE in `backend/routers/events.py`:
- **`recognition_logs`** (partitioned by month): every face/plate detection. Key fields: `event_id` (UUID PK), `match_score` (face cosine similarity 0–1), `ocr_confidence` (LPR), `is_stranger`, `label`, `person_id`, `plate_number`, `metadata_json` (contains `person_name`, `person_role`, `image_path`).
- **`access_events`** (integer PK): alerts only, written by `blacklist_engine`. Key fields: `event_type`, `severity`, `entity_id`, `reason`, `alert_sent`, `json_path`.

The CTE in `_UNIFIED_EVENTS_CTE` maps both tables to a common shape. The `id` in the unified view is a UUID string for recognition_logs and an integer string for access_events — the detail endpoint (`GET /api/events/{id}/detail`) uses this to route to the correct table and return richer fields.

### Backend layout
`backend/main.py` mounts routers under `/api/*`: `auth, cameras, users, vehicles, events, doors, strangers, metrics, stream, enroll, images, settings, face-search`. `enroll.py` proxies to `AI_CORE_ENROLL_URL` (the in-process HTTP server at port 8090 inside `savant-ai-core`). `database.py` uses `asyncpg` with `init_db()` called from a lifespan hook.

**Auth**: Two mechanisms in `backend/routers/auth.py`:
- JWT Bearer (dashboard): `require_jwt` dependency. `require_jwt_query_or_header` also accepts `?t=` query param — used by SSE and image endpoints where `<img src>` / `EventSource` cannot set headers.
- API Key (`X-API-Key` header): `require_api_key` dependency, used by AI Core's `/api/events/ingest`.

### Dashboard frontend layout
`dashboard/src/` — React 18 + Vite + TypeScript + SWR + lucide-react icons:
- `api.ts` — all backend calls; exports `authApi`, `eventsApi`, `camerasApi`, `usersApi`, `vehiclesApi`, `enrollApi`, `strangersApi`, `settingsApi`, `streamApi`, `faceSearchApi`, `imagesApi`. Also exports `getToken`, `setToken`, `clearToken`, `isLoggedIn`, `isDevMode`, `detectImageUrl`.
- `App.tsx` — router: unauthenticated → `LoginPage`; authenticated → `AppShell` wrapping page routes.
- `pages/DashboardPage.tsx` — main dashboard: stat strip, camera grid (left), AI detection log (right). The log uses SSE (`EventSource`) via `eventsApi.streamUrl()` for realtime push. Clicking any event card opens `EventDetailModal` which calls `eventsApi.getDetail(id)` for full AI data including confidence scores.
- `components/AppShell.tsx` — sidebar nav + topbar with user menu (calls `authApi.me` via SWR).

### SSE realtime event flow
`GET /api/events/stream` in `backend/routers/events.py`:
1. On connect: sends last 50 events as `event: snapshot`.
2. When AI Core calls `POST /api/events/ingest`: `_broadcast()` pushes the new event dict to all connected `asyncio.Queue` instances (one per SSE client).
3. Each client receives `event: new_event` immediately.
4. Keepalive comment `: ka` every 20 s prevents Nginx/proxy timeouts.

Frontend `useRealtimeEvents` hook in `DashboardPage.tsx` opens an `EventSource`, handles `snapshot` (replaces list) and `new_event` (prepends + flash highlight), auto-reconnects after 5 s on error. In dev mode (`isDevMode()`) SSE is skipped.

### Configuration & camera/zone state
- `module/module.yml` is the canonical pipeline config. Per-camera ROI zones (`roi_zones`) and `camera_zones` start empty `{}` — they are loaded from the Postgres `cameras` table at runtime. Editing the YAML for these maps is wrong; update the DB and let PG NOTIFY propagate.
- `config/go2rtc.yaml` — go2rtc broker config. Streams added/removed dynamically by `src/ingress/go2rtc_sync.py` listening on `LISTEN cameras_changed`.
- `tracker/config_tracker_NvSORT.yml` — DeepStream NvSORT tracker tuning.

### Output artifact convention
Successful detections are saved as image crops + JSON sidecar to `./Detect/{camera_id}/{date}/{category}/`. The `Detect/` volume is bind-mounted into `savant-ai-core`. Backend serves these via `GET /api/detect-images/{path}?t=<jwt>`.

## Frontend page status

Each page in `dashboard/src/pages/` — current implementation state:

| Page | Lines | Status | Notes |
|---|---|---|---|
| `DashboardPage` | 984 | ✅ Complete | SSE realtime, camera grid, event detail modal, 3-tab log (face/lpr/behavior) |
| `UsersPage` | 978 | ✅ Complete | CRUD + face enrollment + face search modal |
| `BehaviorPage` | 698 | ✅ Complete | Filters by event_type, severity, camera; paginated card grid |
| `StrangersPage` | 628 | ✅ Complete | Grid + detail drawer + image gallery + dedup trigger |
| `LprPage` | 518 | ⚠️ Mock data | Uses hardcoded `MOCK_LPR` array. Needs connection to real `eventsApi.list({ event_type: 'lpr_recognition' })` |
| `CamerasPage` | 321 | ✅ Complete | CRUD + ROI polygon editor + snapshot preview |
| `SettingsPage` | 316 | ✅ Complete | Key-value settings + manual cleanup trigger + cleanup run history |
| `VehiclesPage` | 206 | ✅ Basic | List + create + blacklist toggle. No search, no bulk ops |
| `AlertsPage` | ~250 | ✅ Complete | Paginated alert list with severity filter |
| `EventsPage` | 177 | ⚠️ Minimal | Basic paginated table, no detail modal, no images, no date range |

### LPR events backend CTE bug

`backend/routers/events.py` `_UNIFIED_EVENTS_CTE` maps `recognition_logs` rows with `CASE WHEN label = 'plate' THEN 'lpr_recognition'`. But `blacklist_engine.py` writes `label = obj_meta.label` which is `'car'`, `'motorcycle'`, etc. — never `'plate'`. So normal LPR recognitions never appear as `event_type = 'lpr_recognition'` in the unified event list. Fix: change the condition to `WHEN plate_number IS NOT NULL THEN 'lpr_recognition'`.

## Known bugs fixed (Apr 2026)

- **`src/lpr/plate_ocr.py` `_PPOCR_CHARSET`** was in wrong order (started with `'0'` instead of `' '`). Standard PP-OCRv4 en_dict.txt is ASCII sorted 0x20→0x7E. This caused every OCR character to decode to the wrong glyph (e.g. `'A'`→`'Q'`, `'2'`→`'B'`), so no plate ever passed `_classify_plate()`.
- **`src/business/blacklist_engine.py` `_process_behavior_alerts`** had no rate-limiting before calling `AuditLogger.log_blacklist_event`. `FightingDetector.last_result` persists as `True` for up to `stride=8` frames after a positive inference, producing dozens of identical audit JSON files per second. Fix: `_behavior_last_logged` dict + `_should_log()` with 60 s dedup per event-type per source.
- **`src/analytics/behavior_engine.py` `FightingDetector.add_frame`** returned stale `last_result["fighting"]=True` indefinitely after a positive inference. Fix: expire stale positive after `2×stride` frames without a new inference (`_last_infer_frame` counter).

## Project-specific gotchas

- **NVIDIA Persistence Mode is mandatory on Linux hosts** (`sudo systemctl enable nvidia-persistenced`). Without it, the GPU pipeline deadlocks when the screen locks/sleeps — symptoms are ZMQ queue overflow and GStreamer `Cannot accomplish in current state`.
- **First container start is slow (5–10 min)** while TensorRT compiles ONNX → `.engine` for the host GPU. Engines are cached at `models/engine/`. Don't kill the container during this.
- **Do not install pip `opencv-python*` into the AI Core image.** It shadows the DeepStream-bundled `cv2` and breaks features like `cv2.cuda.ALPHA_OVER`. `Dockerfile.savant-ai-core` explicitly uninstalls it after PaddleOCR pulls it in.
- **Numpy is pinned to `<2`** because Savant 0.6.x and several models require it. Do not upgrade.
- **`output_frame.codec` in `module.yml` is currently `raw-rgb24`** (WSL2-friendly). Switch to `codec: h264, encoder: nvenc` for Linux production deployments.
- **Port mapping deliberately offsets from a co-deployed VMS instance**: postgres host port 5433 (not 5432), go2rtc HTTP 1985 (not 1984), AI Core 9080 (not 8080), grafana 3001 (not 3000). Don't "fix" these to defaults without checking.
- **`recognition_logs` is partitioned by month.** New partitions must exist before data arrives — `ensure_recognition_log_partitions()` runs at startup and via APScheduler cron at 02:00 daily. If a partition is missing, inserts silently fail.
- **SSE connections go through Nginx** in production — the `X-Accel-Buffering: no` response header on `/api/events/stream` is required to prevent Nginx from buffering the stream.
- The `.venv/` directory inside the repo root is ignored by git but is what local (non-Docker) development uses against `requirements-dev.txt`.
- **`plate_detector_sgie` in `module.yml` only runs on `yolov8_primary.car` objects** — motorcycle/bus/truck plates fall back to ORT `_detect_plates()` inside `PlateOCR.process_frame()`. The sGIE therefore doesn't help those labels.
- **`FaceRecognizer` and `enrollment_service` must both call `face_align.align_face()`** with the same `ARCFACE_DST` template. Mismatched alignment produces cosine similarity < 0.3 for known persons, causing them to be treated as strangers.
