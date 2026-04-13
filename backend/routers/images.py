"""
Router quản lý ảnh snapshot — FastAPI backend SV-PRO.

Lưu ảnh capture từ AI event (blacklist person/vehicle, stranger detection).
Backend trả về URLs để frontend xem/truy cập ảnh.

Endpoints:
  GET    /api/images          — Danh sách ảnh (filter: camera_id, entity_id, date range)
  GET    /api/images/{id}     — Chi tiết 1 ảnh
  POST   /api/images          — AI Core upload ảnh mới (multipart/form-data)
  DELETE /api/images/{id}     — Xóa ảnh
"""

import os
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel

from ..database import get_db
from .auth import require_jwt, require_api_key

router = APIRouter()
logger = logging.getLogger("images")

# Thư mục lưu ảnh — config qua env
SNAPSHOT_DIR = os.environ.get("SNAPSHOT_DIR", "/data/snapshots")
Path(SNAPSHOT_DIR).mkdir(parents=True, exist_ok=True)

THUMBNAIL_DIR = os.environ.get("THUMBNAIL_DIR", "/data/snapshots/thumbnails")
Path(THUMBNAIL_DIR).mkdir(parents=True, exist_ok=True)

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}


# ── Pydantic models ─────────────────────────────────────────────────────────────

class ImageOut(BaseModel):
    id:             int
    camera_id:      str
    event_id:       Optional[str]
    entity_id:      Optional[str]
    entity_type:    Optional[str]
    image_path:     str
    thumbnail_path: Optional[str]
    storage_type:   str
    width:          Optional[int]
    height:         Optional[int]
    file_size_bytes: Optional[int]
    detected_at:    str
    created_at:     str


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _save_upload_file(file: UploadFile, dest_dir: str) -> tuple[str, int]:
    """Lưu upload file, trả về (path, size_bytes)."""
    content = await file.read()
    size = len(content)
    if size > MAX_IMAGE_SIZE:
        raise HTTPException(400, f"File quá lớn (> {MAX_IMAGE_SIZE // 1024 // 1024}MB)")

    ext = os.path.splitext(file.filename or ".jpg")[1] or ".jpg"
    fname = f"{uuid.uuid4().hex}{ext}"
    dest_path = Path(dest_dir) / fname
    with open(dest_path, "wb") as f:
        f.write(content)
    return str(dest_path), size


# ── Endpoints ───────────────────────────────────────────────────────────────────

@router.get("/api/images", response_model=list[ImageOut], summary="Danh sách ảnh")
async def list_images(
    camera_id:  Optional[str]   = Query(None),
    entity_id:  Optional[str]   = Query(None),
    entity_type: Optional[str]  = Query(None),
    from_date:  Optional[str]   = Query(None, alias="from"),
    to_date:    Optional[str]   = Query(None, alias="to"),
    limit:      int             = Query(50, ge=1, le=200),
    offset:     int             = Query(0, ge=0),
    db=Depends(get_db),
    _=Depends(require_jwt),
):
    """Lấy danh sách ảnh snapshot với bộ lọc."""
    conditions = []
    values = []
    idx = 1

    if camera_id:
        conditions.append(f"camera_id = ${idx}")
        values.append(camera_id)
        idx += 1
    if entity_id:
        conditions.append(f"entity_id = ${idx}")
        values.append(entity_id)
        idx += 1
    if entity_type:
        conditions.append(f"entity_type = ${idx}")
        values.append(entity_type)
        idx += 1
    if from_date:
        conditions.append(f"detected_at >= ${idx}::timestamptz")
        values.append(from_date)
        idx += 1
    if to_date:
        conditions.append(f"detected_at <= ${idx}::timestamptz")
        values.append(to_date)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT id, camera_id, event_id, entity_id, entity_type,
               image_path, thumbnail_path, storage_type,
               width, height, file_size_bytes,
               detected_at::text, created_at::text
        FROM snapshot_images
        {where}
        ORDER BY detected_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
    """
    values.extend([limit, offset])

    rows = await db.fetch(query, *values)
    return [dict(r) for r in rows]


@router.get("/api/images/{image_id}", response_model=ImageOut, summary="Chi tiết ảnh")
async def get_image(image_id: int, db=Depends(get_db), _=Depends(require_jwt)):
    """Lấy thông tin chi tiết 1 ảnh."""
    row = await db.fetchrow(
        """SELECT id, camera_id, event_id, entity_id, entity_type,
                  image_path, thumbnail_path, storage_type,
                  width, height, file_size_bytes,
                  detected_at::text, created_at::text
           FROM snapshot_images WHERE id=$1""", image_id,
    )
    if not row:
        raise HTTPException(404, f"Ảnh {image_id} không tồn tại")
    return dict(row)


@router.post("/api/images", response_model=ImageOut, status_code=201, summary="Upload ảnh")
async def create_image(
    camera_id:   str   = Form(...),
    file:        UploadFile = File(...),
    event_id:    Optional[str] = Form(None),
    entity_id:   Optional[str] = Form(None),
    entity_type: Optional[str] = Form(None),
    detected_at: Optional[str] = Form(None),
    width:       Optional[int] = Form(None),
    height:      Optional[int] = Form(None),
    db=Depends(get_db),
    _=Depends(require_api_key),
):
    """
    AI Core gọi endpoint này để upload ảnh snapshot.
    Dùng X-API-Key auth (internal service).
    """
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"Chỉ hỗ trợ: {', '.join(ALLOWED_TYPES)}")

    image_path, file_size = await _save_upload_file(file, SNAPSHOT_DIR)

    # Tạo thumbnail path (cùng tên, suffix _thumb)
    thumb_name = Path(image_path).stem + "_thumb.jpg"
    thumbnail_path = str(Path(THUMBNAIL_DIR) / thumb_name)

    detected = detected_at or datetime.now(timezone.utc).isoformat()

    row = await db.fetchrow(
        """INSERT INTO snapshot_images
           (camera_id, event_id, entity_id, entity_type, image_path, thumbnail_path,
            width, height, file_size_bytes, detected_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::timestamptz)
           RETURNING id, camera_id, event_id, entity_id, entity_type,
                     image_path, thumbnail_path, storage_type,
                     width, height, file_size_bytes,
                     detected_at::text, created_at::text""",
        camera_id, event_id, entity_id, entity_type,
        image_path, thumbnail_path, width, height, file_size, detected,
    )
    return dict(row)


@router.delete("/api/images/{image_id}", status_code=204, summary="Xóa ảnh")
async def delete_image(image_id: int, db=Depends(get_db), _=Depends(require_jwt)):
    """Xóa ảnh khỏi DB và filesystem."""
    row = await db.fetchrow(
        "SELECT image_path, thumbnail_path FROM snapshot_images WHERE id=$1", image_id,
    )
    if not row:
        raise HTTPException(404, f"Ảnh {image_id} không tồn tại")

    # Xóa file trên disk
    for p in [row["image_path"], row["thumbnail_path"]]:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError as exc:
                logger.warning("Failed to delete file %s: %s", p, exc)

    await db.execute("DELETE FROM snapshot_images WHERE id=$1", image_id)