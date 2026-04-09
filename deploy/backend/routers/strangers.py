"""
Router Strangers — FastAPI backend SV-PRO.

Endpoints:
  GET /api/strangers               — Danh sách người lạ đã được track
  GET /api/strangers/{uid}         — Chi tiết 1 stranger
  DELETE /api/strangers/{uid}      — Xóa stranger (đã nhận dạng được danh tính)
  POST /api/strangers/{uid}/notes  — Thêm ghi chú cho stranger

Dữ liệu stranger lưu trong bảng guest_faces (xem schema.sql).
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
    stranger_id:    str
    source_id:       Optional[str]
    first_seen:      Optional[str]
    last_seen:       Optional[str]
    quality_frames:   int
    notes:            Optional[str]


class NoteIn(BaseModel):
    """Ghi chú của operator về stranger."""
    notes: str


# ── Endpoints ───────────────────────────────────────────────────────────────────

@router.get("", response_model=list[StrangerOut], summary="Danh sách người lạ")
async def list_strangers(
    source_id: Optional[str] = None,
    limit:     int = 50,
    offset:    int = 0,
    db=Depends(get_db),
    _=Depends(require_jwt),
):
    """
    Trả về danh sách người lạ, sắp xếp theo lần xuất hiện gần nhất.
    Lọc theo source_id nếu cung cấp.
    """
    conditions = []
    params: list = []

    if source_id:
        params.append(source_id)
        conditions.append(f"source_id = ${len(conditions)+1}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    rows = await db.fetch(
        f"""SELECT stranger_id, source_id,
                   first_seen::text, last_seen::text,
                   COALESCE(quality_frames, 0) AS quality_frames, COALESCE(metadata_json->>'notes', '') AS notes
            FROM guest_faces {where}
            ORDER BY last_seen DESC
            LIMIT ${len(conditions)+1} OFFSET ${len(conditions)+2}""",
        *params,
    )
    return [dict(r) for r in rows]


@router.get("/{uid}", response_model=StrangerOut, summary="Chi tiết người lạ")
async def get_stranger(uid: str, db=Depends(get_db), _=Depends(require_jwt)):
    """Chi tiết một stranger theo stranger_id."""
    row = await db.fetchrow(
        """SELECT stranger_id, source_id,
                  first_seen::text, last_seen::text,
                  COALESCE(quality_frames, 0) AS quality_frames, COALESCE(metadata_json->>'notes', '') AS notes
           FROM guest_faces WHERE stranger_id = $1""",
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
    result = await db.execute("DELETE FROM guest_faces WHERE stranger_id = $1", uid)
    if result == 0:
        raise HTTPException(status_code=404, detail="Stranger không tồn tại")


@router.post("/{uid}/notes", response_model=StrangerOut, summary="Thêm ghi chú")
async def add_notes(uid: str, body: NoteIn, db=Depends(get_db), _=Depends(require_jwt)):
    """Operator thêm ghi chú thủ công vào hồ sơ stranger."""
    row = await db.fetchrow(
        """UPDATE guest_faces
           SET metadata_json = jsonb_set(COALESCE(metadata_json, '{}'), '{notes}', to_jsonb($2::text))
           WHERE stranger_id = $1
           RETURNING stranger_id, source_id,
                     first_seen::text, last_seen::text,
                     COALESCE(quality_frames, 0) AS quality_frames, COALESCE(metadata_json->>'notes', '') AS notes""",
        uid, body.notes,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Stranger không tồn tại")
    return dict(row)
