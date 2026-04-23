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
    # id có thể là số nguyên (access_events) hoặc UUID string (recognition_logs)
    # → string union để FE dùng làm React key.
    id:             str
    event_type:     str
    entity_type:    Optional[str] = None
    entity_id:      Optional[str] = None
    severity:       str
    camera_id:      Optional[str] = None
    source_id:      Optional[str] = None
    reason:         Optional[str] = None
    event_timestamp: str
    alert_sent:     bool = False
    # Đường dẫn tương đối ảnh (so với /Detect) — FE build URL: /api/detect-images/{image_path}
    image_path:     Optional[str] = None


# ── Endpoints ───────────────────────────────────────────────────────────────────

_UNIFIED_EVENTS_CTE = """
WITH unified AS (
    -- access_events: alert sự kiện do business_logic (BlacklistEngine) phát.
    SELECT
        id::text                         AS id,
        event_type,
        entity_type,
        entity_id,
        severity,
        camera_id,
        source_id,
        reason,
        event_timestamp,
        COALESCE(alert_sent, false)      AS alert_sent,
        json_path                        AS image_path
    FROM access_events

    UNION ALL

    -- recognition_logs: mọi face/plate detection (kể cả stranger).
    SELECT
        event_id::text                   AS id,
        CASE
            WHEN label = 'plate'         THEN 'lpr_recognition'
            WHEN is_stranger             THEN 'stranger_detected'
            ELSE                              'face_recognition'
        END                              AS event_type,
        CASE WHEN label = 'plate'        THEN 'plate' ELSE 'person' END AS entity_type,
        COALESCE(person_id, plate_number) AS entity_id,
        CASE WHEN is_stranger            THEN 'HIGH'  ELSE 'MEDIUM' END AS severity,
        camera_id,
        source_id,
        COALESCE(metadata_json->>'person_name', plate_category, label) AS reason,
        created_at                       AS event_timestamp,
        false                            AS alert_sent,
        metadata_json->>'image_path'     AS image_path
    FROM recognition_logs
)
"""


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
    Lấy danh sách sự kiện UNION từ:
      - access_events     (alert do BlacklistEngine phát)
      - recognition_logs  (mọi face/plate detection bao gồm stranger)
    Sắp xếp mới nhất trước. Mặc định lấy 50 bản ghi.
    """
    conditions: list[str] = []
    params: list = []

    if camera_id:
        params.append(camera_id);    conditions.append(f"camera_id = ${len(params)}")
    if severity:
        params.append(severity);     conditions.append(f"severity = ${len(params)}")
    if event_type:
        params.append(event_type);   conditions.append(f"event_type = ${len(params)}")
    if from_dt:
        params.append(from_dt);      conditions.append(f"event_timestamp >= ${len(params)}::timestamptz")
    if to_dt:
        params.append(to_dt);        conditions.append(f"event_timestamp <= ${len(params)}::timestamptz")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    rows = await db.fetch(
        f"{_UNIFIED_EVENTS_CTE}"
        f"SELECT id, event_type, entity_type, entity_id, severity, camera_id, source_id, "
        f"       reason, event_timestamp::text, alert_sent, image_path "
        f"FROM unified {where} "
        f"ORDER BY event_timestamp DESC "
        f"LIMIT ${len(conditions)+1} OFFSET ${len(conditions)+2}",
        *params,
    )
    if rows is None:
        return []
    return [dict(r) for r in rows]


@router.get("/stats", summary="Thống kê sự kiện hôm nay")
async def event_stats(db=Depends(get_db), _=Depends(require_jwt)):
    """Tóm tắt số lượng sự kiện hôm nay theo severity và camera (UNION 2 bảng)."""
    today = datetime.now(_VN_TZ).date()
    today_str = today.isoformat()
    try:
        by_severity = await db.fetch(
            f"{_UNIFIED_EVENTS_CTE}"
            "SELECT severity, COUNT(*) AS count FROM unified "
            "WHERE event_timestamp::date = $1 GROUP BY severity", today,
        )
    except Exception:
        by_severity = []
    try:
        by_camera = await db.fetch(
            f"{_UNIFIED_EVENTS_CTE}"
            "SELECT camera_id, COUNT(*) AS count FROM unified "
            "WHERE event_timestamp::date = $1 GROUP BY camera_id "
            "ORDER BY count DESC LIMIT 10", today,
        )
    except Exception:
        by_camera = []
    try:
        total = await db.fetchval(
            f"{_UNIFIED_EVENTS_CTE}"
            "SELECT COUNT(*) FROM unified WHERE event_timestamp::date=$1", today,
        )
    except Exception:
        total = 0
    return {
        "date":        today_str,
        "total":       total or 0,
        "by_severity": {r["severity"]: r["count"] for r in by_severity},
        "top_cameras": [{"camera_id": r["camera_id"], "count": r["count"]} for r in by_camera],
    }


@router.get("/{event_id}", response_model=EventOut, summary="Chi tiết sự kiện")
async def get_event(event_id: str, db=Depends(get_db), _=Depends(require_jwt)):
    """Chi tiết 1 sự kiện theo ID — tìm trong cả access_events và recognition_logs."""
    row = await db.fetchrow(
        f"{_UNIFIED_EVENTS_CTE}"
        "SELECT id, event_type, entity_type, entity_id, severity, camera_id, source_id, "
        "       reason, event_timestamp::text, alert_sent, image_path "
        "FROM unified WHERE id = $1",
        event_id,
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
