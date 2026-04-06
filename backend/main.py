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

from .routers import cameras, users, vehicles, events, health, auth, doors, strangers
from .database import init_db

# ── Cấu hình từ biến môi trường ────────────────────────────────────────────────
APP_TITLE   = "SV-PRO API"
APP_VERSION = "1.0.0"
APP_DESC    = "API backend cho hệ thống nhận diện khuôn mặt và biển số xe SV-PRO."

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle hook: khởi tạo kết nối DB và các tài nguyên khi server start.
    Dọn dẹp khi server shutdown.
    """
    # Startup
    await init_db()
    yield
    # Shutdown — không cần làm gì thêm (connection pool tự đóng)


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

# ── Gắn các router ──────────────────────────────────────────────────────────────
app.include_router(health.router,    prefix="",              tags=["Health"])
app.include_router(auth.router,      prefix="/api/auth",     tags=["Auth"])
app.include_router(cameras.router,   prefix="/api/cameras",  tags=["Cameras"])
app.include_router(users.router,     prefix="/api/users",    tags=["Users"])
app.include_router(vehicles.router,  prefix="/api/vehicles", tags=["Vehicles"])
app.include_router(events.router,    prefix="/api/events",   tags=["Events"])
app.include_router(doors.router,     prefix="/api/doors",    tags=["Doors"])
app.include_router(strangers.router, prefix="/api/strangers",tags=["Strangers"])
