"""
Router CRUD cho Camera management — FastAPI backend SV-PRO.

Endpoints:
  GET    /api/cameras             — Danh sách tất cả camera
  POST   /api/cameras             — Thêm camera mới
  GET    /api/cameras/{id}        — Chi tiết 1 camera
  PATCH  /api/cameras/{id}        — Cập nhật cấu hình camera
  DELETE /api/cameras/{id}        — Xóa camera
  GET    /api/cameras/pipeline/config  — Cấu hình pipeline động từ DB
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from ..go2rtc_client import (
    add_or_update_stream as g2r_add,
    remove_stream as g2r_remove,
    resolve_source_id as g2r_sid,
)
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
    roi_polygon: Optional[list[dict]] = None   # [{x,y}…] normalized [0,1]

class CameraUpdate(BaseModel):
    name:       Optional[str]  = None
    rtsp_url:   Optional[str]  = None
    location:   Optional[str]  = None
    zone:       Optional[str]  = None
    ai_mode:    Optional[str]  = None
    fps_limit:  Optional[int]  = None
    enabled:    Optional[bool] = None
    roi_polygon: Optional[list[dict]] = None

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
    roi_polygon: Optional[list[dict]] = None


# ── Pipeline config cache ───────────────────────────────────────────────────────

_PIPELINE_CACHE: dict = {}
_PIPELINE_CACHE_TTL: float = 30.0


def _invalidate_pipeline_cache() -> None:
    _PIPELINE_CACHE.pop("_expire", None)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("", response_model=list[CameraOut], summary="Danh sách camera")
async def list_cameras(db=Depends(get_db), _=Depends(require_jwt)):
    """Trả về danh sách tất cả camera đã đăng ký trong hệ thống."""
    rows = await db.fetch(
        "SELECT id, name, rtsp_url, location, zone, ai_mode, fps_limit, enabled, created_at::text, roi_polygon "
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
    # asyncpg JSONB codec đã set ở init_db → truyền list/dict trực tiếp.
    row = await db.fetchrow(
        """INSERT INTO cameras (name, rtsp_url, location, zone, ai_mode, fps_limit, enabled, roi_polygon)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
           RETURNING id, name, rtsp_url, location, zone, ai_mode, fps_limit, enabled, created_at::text, roi_polygon""",
        body.name, body.rtsp_url, body.location, body.zone,
        body.ai_mode, body.fps_limit, body.enabled, body.roi_polygon,
    )
    _invalidate_pipeline_cache()
    # Đẩy ngay sang go2rtc — không chờ rtsp_ingest poll 30s
    cam = dict(row)
    await g2r_add(cam)
    return cam


@router.get("/{cam_id}", response_model=CameraOut, summary="Chi tiết camera")
async def get_camera(cam_id: int, db=Depends(get_db), _=Depends(require_jwt)):
    """Lấy thông tin chi tiết của 1 camera theo ID."""
    row = await db.fetchrow(
        "SELECT id, name, rtsp_url, location, zone, ai_mode, fps_limit, enabled, created_at::text, roi_polygon "
        "FROM cameras WHERE id=$1", cam_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Camera {cam_id} không tồn tại")
    return dict(row)


@router.patch("/{cam_id}", response_model=CameraOut, summary="Cập nhật camera")
async def update_camera(cam_id: int, body: CameraUpdate, db=Depends(get_db), _=Depends(require_jwt)):
    """Cập nhật một hoặc nhiều trường của camera. Các trường không truyền sẽ giữ nguyên."""
    # exclude_unset để phân biệt "không gửi" vs "gửi null". roi_polygon=null
    # là cách FE clear ROI → cho phép NULL chỉ với key này.
    raw = body.model_dump(exclude_unset=True)
    updates: dict = {}
    for k, v in raw.items():
        if k == "roi_polygon":
            updates[k] = v   # cho phép null để xoá ROI
        elif v is not None:
            updates[k] = v
    if not updates:
        raise HTTPException(status_code=400, detail="Không có trường nào được cập nhật")

    # asyncpg JSONB codec đã set → list/dict pass trực tiếp.
    set_parts = []
    values: list = [cam_id]
    for k, v in updates.items():
        values.append(v)
        set_parts.append(f"{k} = ${len(values)}")
    set_clause = ", ".join(set_parts)

    row = await db.fetchrow(
        f"UPDATE cameras SET {set_clause} WHERE id=$1 "
        f"RETURNING id, name, rtsp_url, location, zone, ai_mode, fps_limit, enabled, created_at::text, roi_polygon",
        *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Camera {cam_id} không tồn tại")
    _invalidate_pipeline_cache()
    cam = dict(row)
    sid = g2r_sid(cam)
    if cam.get("enabled") and cam.get("rtsp_url"):
        await g2r_add(cam)        # update or add — go2rtc PUT idempotent
    else:
        await g2r_remove(sid)     # disabled / no URL → drop từ go2rtc
    return cam


@router.delete("/{cam_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Xóa camera")
async def delete_camera(cam_id: int, db=Depends(get_db), _=Depends(require_jwt)):
    """Xóa camera khỏi hệ thống. Dữ liệu log liên quan sẽ được giữ nguyên."""
    # Lấy name TRƯỚC khi xoá để remove khỏi go2rtc đúng source_id.
    pre = await db.fetchrow("SELECT id, name FROM cameras WHERE id=$1", cam_id)
    result = await db.execute("DELETE FROM cameras WHERE id=$1", cam_id)
    if result == 0:
        raise HTTPException(status_code=404, detail=f"Camera {cam_id} không tồn tại")
    _invalidate_pipeline_cache()
    if pre:
        await g2r_remove(g2r_sid(dict(pre)))


@router.get("/pipeline/config", summary="Pipeline config động từ DB")
async def get_pipeline_config(
    db=Depends(get_db),
    _=Depends(require_jwt),
):
    """
    Trả về cấu hình pipeline cho AI Core (Savant), động từ DB.

    Response:
      camera_zones : {source_id → zone_name}  — ví dụ {cam_9: gate, cam_10: parking}
      ai_modes    : {source_id → ai_mode}      — lpr | fr | both | off
      sources     : [source_id]                — danh sách tất cả camera đang enabled
    """
    import time
    now = time.time()
    if _PIPELINE_CACHE.get("_expire", 0) > now:
        return _PIPELINE_CACHE

    rows = await db.fetch(
        "SELECT id, zone, ai_mode FROM cameras WHERE enabled=true ORDER BY id"
    )

    camera_zones: dict[str, str] = {}
    ai_modes: dict[str, str] = {}
    sources: list[str] = []

    for r in rows:
        sid = f"cam_{r['id']}"
        sources.append(sid)
        if r["zone"]:
            camera_zones[sid] = r["zone"]
        ai_modes[sid] = r["ai_mode"] or "both"

    result = {
        "camera_zones": camera_zones,
        "ai_modes": ai_modes,
        "sources": sources,
    }
    _PIPELINE_CACHE.clear()
    _PIPELINE_CACHE.update(result)
    _PIPELINE_CACHE["_expire"] = now + _PIPELINE_CACHE_TTL
    return result
