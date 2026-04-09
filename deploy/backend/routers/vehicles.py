"""
Router Vehicles & Events — FastAPI backend SV-PRO Sprint 5.
"""
from typing import Optional
from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from ..database import get_db
from .auth import require_jwt

# ── Vehicles ────────────────────────────────────────────────────────────────────
router = APIRouter()

class VehicleCreate(BaseModel):
    plate_number:    str
    plate_category:  Optional[str] = None
    owner_id:        Optional[int] = None
    is_blacklisted:  bool = False
    blacklist_reason: Optional[str] = None

class VehicleOut(BaseModel):
    id:              int
    plate_number:    str
    plate_category:  Optional[str]
    is_blacklisted:  bool
    blacklist_reason: Optional[str]
    registered_at:   str


@router.get("", response_model=list[VehicleOut], summary="Danh sách phương tiện")
async def list_vehicles(
    blacklisted_only: bool = False,
    limit: int = 100,
    db=Depends(get_db), _=Depends(require_jwt),
):
    """Danh sách xe với tùy chọn lọc chỉ xe blacklist."""
    where = "WHERE is_blacklisted=TRUE" if blacklisted_only else ""
    rows  = await db.fetch(
        f"SELECT id, plate_number, plate_category, is_blacklisted, blacklist_reason, registered_at::text "
        f"FROM vehicles {where} ORDER BY id LIMIT $1", limit
    )
    return [dict(r) for r in rows]


@router.post("", response_model=VehicleOut, status_code=201, summary="Thêm xe")
async def create_vehicle(body: VehicleCreate, db=Depends(get_db), _=Depends(require_jwt)):
    """Thêm xe mới. Nếu is_blacklisted=True phải truyền blacklist_reason."""
    row = await db.fetchrow(
        """INSERT INTO vehicles (plate_number, plate_category, owner_id, is_blacklisted, blacklist_reason)
           VALUES ($1,$2,$3,$4,$5)
           RETURNING id, plate_number, plate_category, is_blacklisted, blacklist_reason, registered_at::text""",
        body.plate_number, body.plate_category, body.owner_id,
        body.is_blacklisted, body.blacklist_reason,
    )
    return dict(row)


@router.patch("/{plate}/blacklist", summary="Thêm/xóa xe khỏi blacklist")
async def toggle_blacklist(
    plate: str,
    blacklisted: bool = Body(...),
    reason: Optional[str] = Body(None),
    db=Depends(get_db), _=Depends(require_jwt),
):
    """Cập nhật trạng thái blacklist nhanh cho 1 xe theo biển số."""
    result = await db.execute(
        "UPDATE vehicles SET is_blacklisted=$1, blacklist_reason=$2, updated_at=NOW() WHERE plate_number=$3",
        blacklisted, reason, plate,
    )
    if result == 0:
        raise HTTPException(status_code=404, detail=f"Xe {plate} không tồn tại")
    return {"plate_number": plate, "is_blacklisted": blacklisted}
