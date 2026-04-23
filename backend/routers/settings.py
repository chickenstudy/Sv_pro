"""
Router App Settings — quản lý cấu hình runtime (key-value JSONB).

Endpoints:
  GET  /api/settings                 — toàn bộ settings
  GET  /api/settings/{key}           — 1 setting
  PUT  /api/settings/{key}           — cập nhật value (yêu cầu JWT)
  POST /api/settings/cleanup/run     — chạy retention cleanup ngay
  GET  /api/settings/cleanup/runs    — lịch sử các lần cleanup
"""

import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..database import get_db
from .auth import require_jwt
from ..retention_cleanup import run_cleanup_now

router = APIRouter()
logger = logging.getLogger("settings")


class SettingValue(BaseModel):
    value: Any   # JSON-serializable: int, str, bool, dict, list


class SettingOut(BaseModel):
    key:        str
    value:      Any
    updated_at: str
    updated_by: str | None = None


class RetentionRunOut(BaseModel):
    id:           int
    started_at:   str
    finished_at:  str | None = None
    triggered_by: str
    deleted_files: int = 0
    deleted_bytes: int = 0
    deleted_rows:  dict | None = None
    error:        str | None = None


_KNOWN_RETENTION_KEYS = {
    "retention.detect_days":       (1, 3650),  # min, max ngày
    "retention.audit_days":        (1, 3650),
    "retention.events_days":       (1, 3650),
    "retention.guest_faces_days":  (1, 3650),
}


@router.get("", summary="Toàn bộ settings")
async def list_settings(db=Depends(get_db), _=Depends(require_jwt)) -> list[SettingOut]:
    rows = await db.fetch(
        "SELECT key, value, updated_at::text, updated_by FROM app_settings ORDER BY key"
    )
    return [dict(r) for r in rows]


@router.get("/{key}", summary="Lấy 1 setting")
async def get_setting(key: str, db=Depends(get_db), _=Depends(require_jwt)) -> SettingOut:
    row = await db.fetchrow(
        "SELECT key, value, updated_at::text, updated_by FROM app_settings WHERE key = $1",
        key,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' không tồn tại")
    return dict(row)


@router.put("/{key}", summary="Cập nhật setting")
async def update_setting(
    key: str,
    body: SettingValue,
    db=Depends(get_db),
    username: str = Depends(require_jwt),
) -> SettingOut:
    # Validation cho retention keys
    if key in _KNOWN_RETENTION_KEYS:
        if not isinstance(body.value, (int, float)):
            raise HTTPException(status_code=400, detail="Giá trị retention phải là số (ngày)")
        days = int(body.value)
        lo, hi = _KNOWN_RETENTION_KEYS[key]
        if not (lo <= days <= hi):
            raise HTTPException(
                status_code=400,
                detail=f"Giá trị {key} phải trong khoảng [{lo}, {hi}] ngày",
            )
        body.value = days

    # UPSERT — asyncpg JSONB codec đã set ở init_db → truyền giá trị Python trực tiếp.
    row = await db.fetchrow(
        """INSERT INTO app_settings (key, value, updated_at, updated_by)
           VALUES ($1, $2, NOW(), $3)
           ON CONFLICT (key) DO UPDATE SET
             value      = EXCLUDED.value,
             updated_at = NOW(),
             updated_by = EXCLUDED.updated_by
           RETURNING key, value, updated_at::text, updated_by""",
        key, body.value, username,
    )
    logger.info("Setting %s = %s by %s", key, body.value, username)
    return dict(row)


@router.post("/cleanup/run", summary="Chạy retention cleanup ngay")
async def trigger_cleanup(
    db=Depends(get_db),
    username: str = Depends(require_jwt),
) -> dict:
    """Trigger thủ công — admin click 'Chạy dọn dẹp ngay' trên FE."""
    summary = await run_cleanup_now(triggered_by=f"manual:{username}")
    return summary


@router.get("/cleanup/runs", summary="Lịch sử cleanup")
async def list_runs(
    limit: int = 20,
    db=Depends(get_db),
    _=Depends(require_jwt),
) -> list[RetentionRunOut]:
    rows = await db.fetch(
        """SELECT id, started_at::text, finished_at::text, triggered_by,
                  COALESCE(deleted_files, 0) AS deleted_files,
                  COALESCE(deleted_bytes, 0) AS deleted_bytes,
                  deleted_rows, error
           FROM retention_runs ORDER BY started_at DESC LIMIT $1""",
        limit,
    )
    return [dict(r) for r in rows]
