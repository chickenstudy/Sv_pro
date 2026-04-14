"""
Router Events & Access Logs — FastAPI backend SV-PRO Sprint 5.

Endpoints:
  GET /api/events                  — Danh sách access_events (filter + phân trang)
  GET /api/events/stats            — Thống kê nhanh: tổng alert hôm nay
  GET /api/events/{id}             — Chi tiết 1 event
  POST /api/events/ingest          — AI Core gửi kết quả nhận diện (API Key)
"""

from datetime import datetime, date, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from ..database import get_db
from .auth import require_jwt, require_api_key

_VN_TZ = timezone(timedelta(hours=7))
router = APIRouter()


# ── Pydantic ────────────────────────────────────────────────────────────────────

class EventIngest(BaseModel):
    """Payload mà AI Core gửi lên sau mỗi sự kiện nhận diện."""
    event_type:   str
    entity_type:  str
    entity_id:    str
    severity:     str = "MEDIUM"
    camera_id:    str
    source_id:    str
    reason:       Optional[str] = None
    json_path:    Optional[str] = None

class EventOut(BaseModel):
    id:             int
    event_type:     str
    entity_type:    Optional[str]
    entity_id:      Optional[str]
    severity:       str
    camera_id:      Optional[str]
    source_id:      Optional[str]
    reason:         Optional[str]
    event_timestamp: str
    alert_sent:     bool


# ── Endpoints ───────────────────────────────────────────────────────────────────

@router.get("", response_model=list[EventOut], summary="Danh sách sự kiện")
async def list_events(
    camera_id:  Optional[str] = None,
    severity:   Optional[str] = None,
    event_type: Optional[str] = None,
    from_dt:    Optional[str] = Query(None, alias="from"),
    to_dt:      Optional[str] = Query(None, alias="to"),
    limit:      int = 50,
    offset:     int = 0,
    db=Depends(get_db), _=Depends(require_jwt),
):
    """
    Lấy danh sách sự kiện cảnh báo với bộ lọc linh hoạt.
    Sắp xếp mới nhất trước. Mặc định lấy 50 bản ghi.
    """
    conditions = []
    params: list = []

    def add(cond: str, val):
        params.append(val)
        conditions.append(f"{cond} = ${len(params)}")

    if camera_id:  add("camera_id", camera_id)
    if severity:   add("severity", severity)
    if event_type: add("event_type", event_type)
    if from_dt:    add("event_timestamp", from_dt); conditions[-1] += " >="
    if to_dt:      add("event_timestamp", to_dt);   conditions[-1] += " <="

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    rows = await db.fetch(
        f"SELECT id, event_type, entity_type, entity_id, severity, camera_id, source_id, "
        f"reason, event_timestamp::text, alert_sent "
        f"FROM access_events {where} ORDER BY event_timestamp DESC "
        f"LIMIT ${len(conditions)+1} OFFSET ${len(conditions)+2}",
        *params,
    )
    if rows is None:
        return []
    return [dict(r) for r in rows]


@router.get("/stats", summary="Thống kê sự kiện hôm nay")
async def event_stats(db=Depends(get_db), _=Depends(require_jwt)):
    """Tóm tắt số lượng sự kiện cảnh báo hôm nay theo severity và camera."""
    today = datetime.now(_VN_TZ).date()
    today_str = today.isoformat()
    try:
        by_severity = await db.fetch(
            "SELECT severity, COUNT(*) AS count FROM access_events "
            "WHERE event_timestamp::date = $1 GROUP BY severity", today,
        )
    except Exception:
        by_severity = []
    try:
        by_camera = await db.fetch(
            "SELECT camera_id, COUNT(*) AS count FROM access_events "
            "WHERE event_timestamp::date = $1 GROUP BY camera_id ORDER BY count DESC LIMIT 10", today,
        )
    except Exception:
        by_camera = []
    try:
        total = await db.fetchval("SELECT COUNT(*) FROM access_events WHERE event_timestamp::date=$1", today)
    except Exception:
        total = 0
    return {
        "date":        today_str,
        "total":       total or 0,
        "by_severity": {r["severity"]: r["count"] for r in by_severity},
        "top_cameras": [{"camera_id": r["camera_id"], "count": r["count"]} for r in by_camera],
    }


@router.get("/{event_id}", response_model=EventOut, summary="Chi tiết sự kiện")
async def get_event(event_id: int, db=Depends(get_db), _=Depends(require_jwt)):
    """Chi tiết 1 sự kiện cảnh báo theo ID."""
    row = await db.fetchrow(
        "SELECT id, event_type, entity_type, entity_id, severity, camera_id, source_id, "
        "reason, event_timestamp::text, alert_sent FROM access_events WHERE id=$1", event_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Sự kiện không tồn tại")
    return dict(row)


@router.post("/ingest", status_code=201, summary="AI Core gửi sự kiện (Internal)")
async def ingest_event(
    body: EventIngest,
    db=Depends(get_db),
    _=Depends(require_api_key),   # Chỉ AI Core nội bộ mới được gọi endpoint này
):
    """
    Endpoint nội bộ: AI Core gửi kết quả nhận diện blacklist lên để lưu DB.
    Yêu cầu header X-API-Key hợp lệ.
    """
    row = await db.fetchrow(
        """INSERT INTO access_events
             (event_type, entity_type, entity_id, severity, camera_id, source_id, reason, json_path)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
           RETURNING id, event_timestamp::text""",
        body.event_type, body.entity_type, body.entity_id, body.severity,
        body.camera_id, body.source_id, body.reason, body.json_path,
    )
    return {"id": row["id"], "event_timestamp": row["event_timestamp"]}
