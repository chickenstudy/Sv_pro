"""
SV-PRO FastAPI Backend — Sprint 5.

Entry point cho REST API server:
  - CRUD cameras, users, vehicles.
  - Endpoint nhận embedding từ AI Core (gRPC thay thế tạm bằng REST POST).
  - Auth: API Key cho AI Core (header X-API-Key).
  - JWT cho Dashboard (Bearer token).
  - Swagger/OpenAPI tại /docs.
  - Health check tại /health.

Khởi động: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

try:
    from prometheus_fastapi_instrumentator import Instrumentator
except ImportError:  # pragma: no cover
    Instrumentator = None

from .routers import cameras, users, vehicles, events, health, auth, doors, strangers, metrics, stream, enroll, images, detect_images, settings as settings_router, face_search
from .database import init_db
from .retention_cleanup import run_cleanup_now, ensure_recognition_log_partitions

# ── Cấu hình từ biến môi trường ────────────────────────────────────────────────
APP_TITLE   = "SV-PRO API"
APP_VERSION = "1.0.0"
APP_DESC    = "API backend cho hệ thống nhận diện khuôn mặt và biển số xe SV-PRO."

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
# Khi allow_credentials=True, allow_origins không được là ["*"]
# Validate không có wildcard
for origin in CORS_ORIGINS:
    if origin == "*":
        raise ValueError("CORS_ORIGINS không được chứa wildcard '*' khi allow_credentials=True")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle hook: khởi tạo kết nối DB + APScheduler khi server start.
    Dọn dẹp scheduler + pool khi shutdown.
    """
    # ── Startup ──────────────────────────────────────────────────────────────
    await init_db()

    # Đảm bảo partition tháng hiện tại + 2 tháng tới có sẵn ngay khi boot.
    try:
        await ensure_recognition_log_partitions(months_ahead=2)
    except Exception as exc:
        import logging as _log
        _log.getLogger("startup").warning("partition pre-create failed: %s", exc)

    # APScheduler: cron job retention + partition auto-create.
    scheduler = None
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
        scheduler = AsyncIOScheduler(timezone="Asia/Ho_Chi_Minh")
        # Retention: chạy 02:15 mỗi ngày (giờ thấp tải)
        scheduler.add_job(
            run_cleanup_now,
            CronTrigger(hour=2, minute=15),
            kwargs={"triggered_by": "cron"},
            id="retention_cleanup",
            replace_existing=True,
        )
        # Partition: chạy 02:00 mỗi ngày (trước retention 15 phút)
        scheduler.add_job(
            ensure_recognition_log_partitions,
            CronTrigger(hour=2, minute=0),
            kwargs={"months_ahead": 2},
            id="partition_create",
            replace_existing=True,
        )
        scheduler.start()
        app.state.scheduler = scheduler
    except ImportError:
        import logging as _log
        _log.getLogger("startup").warning(
            "APScheduler không cài — retention chỉ chạy được khi gọi tay /api/settings/cleanup/run"
        )

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass


# ── Khởi tạo FastAPI app ────────────────────────────────────────────────────────
app = FastAPI(
    title       = APP_TITLE,
    version     = APP_VERSION,
    description = APP_DESC,
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# CORS — cho phép Dashboard (React) gọi API
app.add_middleware(
    CORSMiddleware,
    allow_origins     = CORS_ORIGINS,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# Prometheus /metrics for backend (scraped by monitoring/prometheus.yml)
if Instrumentator is not None:
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# ── Gắn các router ──────────────────────────────────────────────────────────────
app.include_router(health.router,    prefix="",               tags=["Health"])
app.include_router(auth.router,      prefix="/api/auth",      tags=["Auth"])
app.include_router(cameras.router,   prefix="/api/cameras",   tags=["Cameras"])
app.include_router(users.router,     prefix="/api/users",     tags=["Users"])
app.include_router(vehicles.router,  prefix="/api/vehicles",  tags=["Vehicles"])
app.include_router(events.router,    prefix="/api/events",    tags=["Events"])
app.include_router(doors.router,     prefix="/api/doors",     tags=["Doors"])
app.include_router(strangers.router, prefix="/api/strangers", tags=["Strangers"])
app.include_router(metrics.router,   prefix="/api/metrics",   tags=["Metrics"])
app.include_router(stream.router,   prefix="",               tags=["Stream"])
app.include_router(enroll.router,   prefix="/api/enroll",     tags=["Enrollment"])
app.include_router(images.router,    prefix="/api/images",     tags=["Images"])
app.include_router(detect_images.router, prefix="",            tags=["DetectImages"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["Settings"])
app.include_router(face_search.router, prefix="/api/face-search", tags=["FaceSearch"])
