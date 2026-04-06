"""
Router CRUD Users — FastAPI backend SV-PRO Sprint 5.

Endpoints:
  GET    /api/users                — Danh sách người dùng (phân trang)
  POST   /api/users                — Thêm người dùng mới
  GET    /api/users/{id}           — Chi tiết người dùng
  PATCH  /api/users/{id}           — Cập nhật thông tin / role
  DELETE /api/users/{id}           — Vô hiệu hoá người dùng
  POST   /api/users/{id}/enroll    — Đăng ký khuôn mặt (upload embedding)
"""

import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from ..database import get_db
from .auth import require_jwt

router = APIRouter()


# ── Pydantic models ─────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    person_id:       str
    name:            str
    role:            str = "staff"         # staff | blacklist | guest | admin
    access_zones:    list[str] = []
    blacklist_reason: Optional[str] = None

class UserUpdate(BaseModel):
    name:            Optional[str]       = None
    role:            Optional[str]       = None
    active:          Optional[bool]      = None
    access_zones:    Optional[list[str]] = None
    blacklist_reason: Optional[str]      = None

class FaceEnrollRequest(BaseModel):
    embedding:         list[float]   # Vector 512-dim từ ArcFace
    embedding_version: int = 1

class UserOut(BaseModel):
    id:               int
    person_id:        str
    name:             str
    role:             str
    active:           bool
    access_zones:     list[str]
    has_embedding:    bool
    created_at:       str


# ── Endpoints ───────────────────────────────────────────────────────────────────

@router.get("", response_model=list[UserOut], summary="Danh sách người dùng")
async def list_users(
    role:   Optional[str] = None,
    active: bool = True,
    limit:  int  = 100,
    offset: int  = 0,
    db=Depends(get_db), _=Depends(require_jwt),
):
    """Danh sách người dùng với bộ lọc theo role và trạng thái active."""
    where = "WHERE active = $1"
    params: list = [active]
    if role:
        where += f" AND role = ${len(params)+1}"
        params.append(role)
    params += [limit, offset]

    rows = await db.fetch(
        f"SELECT id, person_id, name, role, active, access_zones, "
        f"(face_embedding IS NOT NULL) AS has_embedding, created_at::text "
        f"FROM users {where} ORDER BY id LIMIT ${len(params)-1} OFFSET ${len(params)}",
        *params,
    )
    return [
        {**dict(r), "access_zones": list(r["access_zones"] or [])}
        for r in rows
    ]


@router.post("", response_model=UserOut, status_code=201, summary="Thêm người dùng")
async def create_user(body: UserCreate, db=Depends(get_db), _=Depends(require_jwt)):
    """
    Thêm người dùng mới. Chưa có embedding (đăng ký khuôn mặt qua endpoint riêng).
    role='blacklist' cần truyền kèm blacklist_reason.
    """
    if body.role == "blacklist" and not body.blacklist_reason:
        raise HTTPException(status_code=400, detail="Thiếu blacklist_reason khi role=blacklist")

    row = await db.fetchrow(
        """INSERT INTO users (person_id, name, role, access_zones, blacklist_reason)
           VALUES ($1,$2,$3,$4,$5)
           RETURNING id, person_id, name, role, active, access_zones,
                     (face_embedding IS NOT NULL) AS has_embedding, created_at::text""",
        body.person_id, body.name, body.role,
        body.access_zones, body.blacklist_reason,
    )
    return {**dict(row), "access_zones": list(row["access_zones"] or [])}


@router.get("/{user_id}", response_model=UserOut, summary="Chi tiết người dùng")
async def get_user(user_id: int, db=Depends(get_db), _=Depends(require_jwt)):
    """Lấy thông tin chi tiết của 1 người dùng theo ID."""
    row = await db.fetchrow(
        "SELECT id, person_id, name, role, active, access_zones, "
        "(face_embedding IS NOT NULL) AS has_embedding, created_at::text "
        "FROM users WHERE id=$1", user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Người dùng không tồn tại")
    return {**dict(row), "access_zones": list(row["access_zones"] or [])}


@router.patch("/{user_id}", response_model=UserOut, summary="Cập nhật người dùng")
async def update_user(user_id: int, body: UserUpdate, db=Depends(get_db), _=Depends(require_jwt)):
    """Cập nhật một hoặc nhiều trường. Đặc biệt: đổi role thành blacklist cần blacklist_reason."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="Không có trường nào được cập nhật")

    set_clause = ", ".join(f"{k}=${i+2}" for i, k in enumerate(updates))
    values     = [user_id] + list(updates.values())
    row = await db.fetchrow(
        f"UPDATE users SET {set_clause}, updated_at=NOW() WHERE id=$1 "
        f"RETURNING id, person_id, name, role, active, access_zones, "
        f"(face_embedding IS NOT NULL) AS has_embedding, created_at::text",
        *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Người dùng không tồn tại")
    return {**dict(row), "access_zones": list(row["access_zones"] or [])}


@router.delete("/{user_id}", status_code=204, summary="Vô hiệu hóa người dùng")
async def deactivate_user(user_id: int, db=Depends(get_db), _=Depends(require_jwt)):
    """Đặt active=False thay vì xóa để giữ lịch sử log nhận diện."""
    result = await db.execute("UPDATE users SET active=FALSE WHERE id=$1", user_id)
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="Người dùng không tồn tại")


@router.post("/{user_id}/enroll", status_code=200, summary="Đăng ký khuôn mặt")
async def enroll_face(user_id: int, body: FaceEnrollRequest, db=Depends(get_db), _=Depends(require_jwt)):
    """
    Lưu embedding khuôn mặt 512-dim cho người dùng.
    Embedding phải được tính bởi ArcFace R100 trước khi gửi.
    Tự động cập nhật embedding_version để tránh so sánh nhầm model cũ/mới.
    """
    if len(body.embedding) != 512:
        raise HTTPException(status_code=400, detail=f"Embedding phải có 512 chiều, nhận được {len(body.embedding)}")

    # Chuyển về pgvector format (string [x,x,x,...])
    emb_str = "[" + ",".join(f"{x:.6f}" for x in body.embedding) + "]"
    result  = await db.execute(
        "UPDATE users SET face_embedding=$1::vector, embedding_version=$2, updated_at=NOW() WHERE id=$3",
        emb_str, body.embedding_version, user_id,
    )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="Người dùng không tồn tại")
    return {"message": f"Đã đăng ký khuôn mặt cho user {user_id}", "embedding_version": body.embedding_version}
