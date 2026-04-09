"""
Router health check cho FastAPI backend SV-PRO.
Trả về trạng thái DB và Redis để monitoring / Docker healthcheck.
"""

import os

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..database import get_db

router = APIRouter()


@router.get("/health", summary="Health check", tags=["Health"])
async def health_check(db=Depends(get_db)):
    """
    Kiểm tra trạng thái hoạt động của:
      - API server (luôn OK nếu endpoint phản hồi)
      - PostgreSQL (thực hiện SELECT 1)
    Docker healthcheck: GET /health phải trả về 200.
    """
    db_ok = False
    try:
        await db.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        pass

    redis_ok = False
    try:
        import redis  # lazy import
        r = redis.Redis(
            host=os.environ.get("REDIS_HOST", "redis"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            db=int(os.environ.get("REDIS_DB", "0")),
            socket_connect_timeout=1,
        )
        redis_ok = bool(r.ping())
    except Exception:
        redis_ok = False

    ok = db_ok and redis_ok
    status_code = 200 if ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status":   "ok" if ok else "degraded",
            "database": "ok" if db_ok else "error",
            "redis":    "ok" if redis_ok else "error",
        },
    )
