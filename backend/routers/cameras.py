"""
Router CRUD cho Camera management — FastAPI backend SV-PRO.

Endpoints:
  GET    /api/cameras          — Danh sách tất cả camera
  POST   /api/cameras          — Thêm camera mới
  GET    /api/cameras/{id}     — Chi tiết 1 camera
  PATCH  /api/cameras/{id}     — Cập nhật cấu hình camera
  DELETE /api/cameras/{id}     — Xóa camera
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from .auth import require_jwt

router = APIRouter()


# ── Pydantic models ─────────────────────────────────────────────────────────────

class CameraCreate(BaseModel):
    name:       str
    rtsp_url:   str
    location:   Optional[str] = None
    zone:       Optional[str] = None
    ai_mode:    str  = "both"       # "lpr" | "fr" | "both" | "off"
    fps_limit:  int  = 10
    enabled:    bool = True

class CameraUpdate(BaseModel):
    name:       Optional[str]  = None
    rtsp_url:   Optional[str]  = None
    location:   Optional[str]  = None
    zone:       Optional[str]  = None
    ai_mode:    Optional[str]  = None
    fps_limit:  Optional[int]  = None
    enabled:    Optional[bool] = None

class CameraOut(BaseModel):
    id:         int
    name:       str
    rtsp_url:   str
    location:   Optional[str]
    zone:       Optional[str]
    ai_mode:    str
    fps_limit:  int
    enabled:    bool
    created_at: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("", response_model=list[CameraOut], summary="Danh sách camera")
async def list_cameras(db=Depends(get_db), _=Depends(require_jwt)):
    """Trả về danh sách tất cả camera đã đăng ký trong hệ thống."""
    rows = await db.fetch(
        "SELECT id, name, rtsp_url, location, zone, ai_mode, fps_limit, enabled, created_at::text "
        "FROM cameras ORDER BY id"
    )
    return [dict(r) for r in rows]


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED, summary="Thêm camera")
async def create_camera(body: CameraCreate, db=Depends(get_db), _=Depends(require_jwt)):
    """
    Thêm camera mới vào hệ thống.
    rtsp_url phải là URL hợp lệ (rtsp://...).
    ai_mode xác định pipeline AI nào sẽ xử lý luồng camera này.
    """
    row = await db.fetchrow(
        """INSERT INTO cameras (name, rtsp_url, location, zone, ai_mode, fps_limit, enabled)
           VALUES ($1,$2,$3,$4,$5,$6,$7)
           RETURNING id, name, rtsp_url, location, zone, ai_mode, fps_limit, enabled, created_at::text""",
        body.name, body.rtsp_url, body.location, body.zone,
        body.ai_mode, body.fps_limit, body.enabled,
    )
    return dict(row)


@router.get("/{cam_id}", response_model=CameraOut, summary="Chi tiết camera")
async def get_camera(cam_id: int, db=Depends(get_db), _=Depends(require_jwt)):
    """Lấy thông tin chi tiết của 1 camera theo ID."""
    row = await db.fetchrow(
        "SELECT id, name, rtsp_url, location, zone, ai_mode, fps_limit, enabled, created_at::text "
        "FROM cameras WHERE id=$1", cam_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Camera {cam_id} không tồn tại")
    return dict(row)


@router.patch("/{cam_id}", response_model=CameraOut, summary="Cập nhật camera")
async def update_camera(cam_id: int, body: CameraUpdate, db=Depends(get_db), _=Depends(require_jwt)):
    """Cập nhật một hoặc nhiều trường của camera. Các trường không truyền sẽ giữ nguyên."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="Không có trường nào được cập nhật")

    set_clause = ", ".join(f"{k}=${i+2}" for i, k in enumerate(updates))
    values     = [cam_id] + list(updates.values())

    row = await db.fetchrow(
        f"UPDATE cameras SET {set_clause} WHERE id=$1 "
        f"RETURNING id, name, rtsp_url, location, zone, ai_mode, fps_limit, enabled, created_at::text",
        *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Camera {cam_id} không tồn tại")
    return dict(row)


@router.delete("/{cam_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Xóa camera")
async def delete_camera(cam_id: int, db=Depends(get_db), _=Depends(require_jwt)):
    """Xóa camera khỏi hệ thống. Dữ liệu log liên quan sẽ được giữ nguyên."""
    result = await db.execute("DELETE FROM cameras WHERE id=$1", cam_id)
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail=f"Camera {cam_id} không tồn tại")
