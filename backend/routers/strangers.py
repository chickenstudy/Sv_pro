"""
Router Strangers — FastAPI backend SV-PRO.

Endpoints:
  GET /api/strangers               — Danh sách người lạ đã được track
  GET /api/strangers/{uid}         — Chi tiết 1 stranger
  DELETE /api/strangers/{uid}      — Xóa stranger (đã nhận dạng được danh tính)
  POST /api/strangers/{uid}/notes  — Thêm ghi chú cho stranger

Dữ liệu stranger lưu trong bảng strangers (xem schema.sql).
AI Core ghi dữ liệu qua /api/events/ingest.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..database import get_db
from .auth import require_jwt

router = APIRouter()


# ── Pydantic schemas ────────────────────────────────────────────────────────────

class StrangerOut(BaseModel):
    """Thông tin một người lạ detected bởi FR pipeline."""
    id:              int
    stranger_uid:    str
    first_seen:      str
    last_seen:       str
    camera_id:       Optional[str]
    source_id:       Optional[str]
    frame_count:     int
    face_crop_path:  Optional[str]
    notes:           Optional[str]


class NoteIn(BaseModel):
    """Ghi chú của operator về stranger."""
    notes: str


# ── Endpoints ───────────────────────────────────────────────────────────────────

@router.get("", response_model=list[StrangerOut], summary="Danh sách người lạ")
async def list_strangers(
    camera_id: Optional[str] = None,
    limit:     int = 50,
    offset:    int = 0,
    db=Depends(get_db),
    _=Depends(require_jwt),
):
    """
    Trả về danh sách người lạ, sắp xếp theo lần xuất hiện gần nhất.
    Lọc theo camera_id nếu cung cấp.
    """
    conditions = []
    params: list = []

    if camera_id:
        params.append(camera_id)
        conditions.append(f"camera_id = ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    rows = await db.fetch(
        f"""SELECT id, stranger_uid,
                   first_seen::text, last_seen::text,
                   camera_id, source_id, frame_count,
                   face_crop_path, notes
            FROM strangers {where}
            ORDER BY last_seen DESC
            LIMIT ${len(params)-1} OFFSET ${len(params)}""",
        *params,
    )
    return [dict(r) for r in rows]


@router.get("/{uid}", response_model=StrangerOut, summary="Chi tiết người lạ")
async def get_stranger(uid: str, db=Depends(get_db), _=Depends(require_jwt)):
    """Chi tiết một stranger theo stranger_uid."""
    row = await db.fetchrow(
        """SELECT id, stranger_uid,
                  first_seen::text, last_seen::text,
                  camera_id, source_id, frame_count,
                  face_crop_path, notes
           FROM strangers WHERE stranger_uid = $1""",
        uid,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Stranger không tồn tại")
    return dict(row)


@router.delete("/{uid}", status_code=204, summary="Xóa stranger")
async def delete_stranger(uid: str, db=Depends(get_db), _=Depends(require_jwt)):
    """
    Xóa stranger khỏi DB (thường dùng khi đã xác định được danh tính và
    tạo tài khoản User chính thức thay thế).
    """
    result = await db.execute("DELETE FROM strangers WHERE stranger_uid = $1", uid)
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Stranger không tồn tại")


@router.post("/{uid}/notes", response_model=StrangerOut, summary="Thêm ghi chú")
async def add_notes(uid: str, body: NoteIn, db=Depends(get_db), _=Depends(require_jwt)):
    """Operator thêm ghi chú thủ công vào hồ sơ stranger."""
    row = await db.fetchrow(
        """UPDATE strangers SET notes = $1 WHERE stranger_uid = $2
           RETURNING id, stranger_uid,
                     first_seen::text, last_seen::text,
                     camera_id, source_id, frame_count,
                     face_crop_path, notes""",
        body.notes, uid,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Stranger không tồn tại")
    return dict(row)
