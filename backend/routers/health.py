"""
Router health check cho FastAPI backend SV-PRO.
Trả về trạng thái DB và Redis để monitoring / Docker healthcheck.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
import asyncpg
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

    status_code = 200 if db_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status":   "ok" if db_ok else "degraded",
            "database": "ok" if db_ok else "error",
        },
    )
