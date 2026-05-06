"""
Router Events & Access Logs — FastAPI backend SV-PRO Sprint 5.

Endpoints:
  GET /api/events                  — Danh sách access_events (filter + phân trang)
  GET /api/events/stats            — Thống kê nhanh: tổng alert hôm nay
  GET /api/events/{id}             — Chi tiết 1 event
  POST /api/events/ingest          — AI Core gửi kết quả nhận diện (API Key)
"""

import asyncio
import json
from datetime import datetime, date, timezone, timedelta
from typing import Annotated, Optional, Set
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from ..database import get_db
from .auth import require_jwt, require_api_key, require_jwt_query_or_header

# ── SSE broadcaster ─────────────────────────────────────────────────────────────
# Mỗi SSE client có 1 asyncio.Queue riêng. Khi AI Core gọi /ingest, event được
# push vào tất cả queues → client nhận ngay, không cần poll.
_sse_clients: Set[asyncio.Queue] = set()

def _broadcast(event_dict: dict) -> None:
    dead: Set[asyncio.Queue] = set()
    for q in _sse_clients:
        try:
            q.put_nowait(event_dict)
        except asyncio.QueueFull:
            dead.add(q)
    _sse_clients -= dead

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
    # Confidence scores (chỉ recognition_logs — null cho access_events)
    match_score:    Optional[float] = None    # face cosine similarity 0..1
    ocr_confidence: Optional[float] = None    # LPR OCR confidence 0..1


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
        json_path                        AS image_path,
        NULL::float                      AS match_score,
        NULL::float                      AS ocr_confidence
    FROM access_events

    UNION ALL

    -- recognition_logs: mọi face/plate detection (kể cả stranger).
    SELECT
        event_id::text                   AS id,
        CASE
            WHEN plate_number IS NOT NULL THEN 'lpr_recognition'
            WHEN is_stranger              THEN 'stranger_detected'
            ELSE                               'face_recognition'
        END                              AS event_type,
        CASE WHEN plate_number IS NOT NULL THEN 'plate' ELSE 'person' END AS entity_type,
        COALESCE(person_id, plate_number) AS entity_id,
        CASE WHEN is_stranger            THEN 'HIGH'  ELSE 'MEDIUM' END AS severity,
        camera_id,
        source_id,
        COALESCE(metadata_json->>'person_name', plate_category, label) AS reason,
        created_at                       AS event_timestamp,
        false                            AS alert_sent,
        metadata_json->>'image_path'     AS image_path,
        match_score::float               AS match_score,
        ocr_confidence::float            AS ocr_confidence
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
    response:   Response = None,
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
        try:
            params.append(datetime.fromisoformat(from_dt))
        except ValueError:
            params.append(datetime.fromisoformat(from_dt.replace('Z', '+00:00')))
        conditions.append(f"event_timestamp >= ${len(params)}")
    if to_dt:
        try:
            params.append(datetime.fromisoformat(to_dt))
        except ValueError:
            params.append(datetime.fromisoformat(to_dt.replace('Z', '+00:00')))
        conditions.append(f"event_timestamp <= ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    # Đếm tổng để trả về qua header X-Total-Count (dùng cho pagination FE)
    count_params = params[: len(conditions)]
    try:
        total_count = await db.fetchval(
            f"{_UNIFIED_EVENTS_CTE}SELECT COUNT(*) FROM unified {where}",
            *count_params,
        ) or 0
    except Exception:
        total_count = 0
    if response is not None:
        response.headers["X-Total-Count"] = str(total_count)
        response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"

    rows = await db.fetch(
        f"{_UNIFIED_EVENTS_CTE}"
        f"SELECT id, event_type, entity_type, entity_id, severity, camera_id, source_id, "
        f"       reason, event_timestamp::text, alert_sent, image_path, match_score, ocr_confidence "
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
    """Tóm tắt số lượng sự kiện hôm nay theo severity, event_type và camera (UNION 2 bảng)."""
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
        by_event_type = await db.fetch(
            f"{_UNIFIED_EVENTS_CTE}"
            "SELECT event_type, COUNT(*) AS count FROM unified "
            "WHERE event_timestamp::date = $1 GROUP BY event_type", today,
        )
    except Exception:
        by_event_type = []
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
        "date":          today_str,
        "total":         total or 0,
        "by_severity":   {r["severity"]: r["count"] for r in by_severity},
        "by_event_type": {r["event_type"]: r["count"] for r in by_event_type},
        "top_cameras":   [{"camera_id": r["camera_id"], "count": r["count"]} for r in by_camera],
    }


@router.get("/stream", summary="SSE — realtime event stream cho Dashboard")
async def stream_events(
    username: Annotated[str, Depends(require_jwt_query_or_header)],
    db: Annotated[object, Depends(get_db)],
):
    """
    Server-Sent Events endpoint. Dashboard mở 1 kết nối duy nhất và nhận push
    mỗi khi AI Core ingest event mới — không cần poll.

    Auth: Bearer header hoặc ?t=<jwt> (EventSource không gắn được header).
    Flow:
      1. Gửi ngay snapshot 50 event mới nhất (event: snapshot).
      2. Giữ kết nối, push event mới khi AI Core gọi /ingest (event: new_event).
      3. Keepalive comment ": ka" mỗi 20s để Nginx/proxy không đóng kết nối.
    """
    async def generator():
        # ── 1. Initial snapshot ─────────────────────────────────────────────
        rows = await db.fetch(
            f"{_UNIFIED_EVENTS_CTE}"
            "SELECT id, event_type, entity_type, entity_id, severity, camera_id, source_id, "
            "       reason, event_timestamp::text, alert_sent, image_path, match_score, ocr_confidence "
            "FROM unified ORDER BY event_timestamp DESC LIMIT 50"
        )
        snapshot = [dict(r) for r in rows]
        yield f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"

        # ── 2. Stream mới ───────────────────────────────────────────────────
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        _sse_clients.add(q)
        try:
            while True:
                try:
                    event_dict = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"event: new_event\ndata: {json.dumps(event_dict)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ka\n\n"
        finally:
            _sse_clients.discard(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{event_id}", response_model=EventOut, summary="Chi tiết sự kiện")
async def get_event(event_id: str, db=Depends(get_db), _=Depends(require_jwt)):
    """Chi tiết 1 sự kiện theo ID — tìm trong cả access_events và recognition_logs."""
    row = await db.fetchrow(
        f"{_UNIFIED_EVENTS_CTE}"
        "SELECT id, event_type, entity_type, entity_id, severity, camera_id, source_id, "
        "       reason, event_timestamp::text, alert_sent, image_path, match_score, ocr_confidence "
        "FROM unified WHERE id = $1",
        event_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Sự kiện không tồn tại")
    return dict(row)


_UUID_RE = __import__('re').compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', __import__('re').IGNORECASE
)


@router.get("/{event_id}/detail", summary="Chi tiết đầy đủ — confidence, metadata AI")
async def get_event_detail(
    event_id: str,
    db: Annotated[object, Depends(get_db)],
    _: Annotated[str, Depends(require_jwt)],
):
    """
    Trả về toàn bộ dữ liệu AI cho 1 event:
    - Nếu ID là UUID → lấy từ recognition_logs (match_score, ocr_confidence, metadata_json, …)
    - Nếu ID là số nguyên → lấy từ access_events (entity, severity, reason, …)
    """
    if _UUID_RE.match(event_id):
        row = await db.fetchrow(
            """
            SELECT
                event_id::text                        AS id,
                'recognition_log'                     AS source,
                label,
                person_id,
                match_score,
                match_score                           AS fr_confidence,  -- canonical name (match_score giữ cho backward compat)
                is_stranger,
                plate_number,
                plate_category,
                ocr_confidence,
                camera_id,
                source_id,
                created_at::text                      AS event_timestamp,
                -- Extract frequently-accessed fields lên top-level để FE không
                -- phải reach vào metadata_json
                metadata_json->>'person_name'         AS person_name,
                metadata_json->>'person_role'         AS person_role,
                metadata_json->>'image_path'          AS image_path,
                metadata_json->>'plate_image_path'    AS plate_image_path,
                metadata_json::text                   AS metadata_raw
            FROM recognition_logs
            WHERE event_id = $1::uuid
            """,
            event_id,
        )
        if row:
            import json as _json
            data = dict(row)
            raw = data.pop("metadata_raw", None)
            data["metadata"] = _json.loads(raw) if raw else {}
            return data

    # Fallback: access_events (integer PK)
    try:
        int_id = int(event_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Sự kiện không tồn tại")

    row = await db.fetchrow(
        """
        SELECT
            id::text                       AS id,
            'access_event'                 AS source,
            event_type,
            entity_type,
            entity_id,
            severity,
            camera_id,
            source_id,
            reason,
            event_timestamp::text,
            alert_sent,
            json_path                      AS image_path
        FROM access_events
        WHERE id = $1
        """,
        int_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Sự kiện không tồn tại")
    return dict(row)


@router.post("/ingest", status_code=201, summary="AI Core gửi sự kiện (Internal)")
async def ingest_event(
    body: EventIngest,
    db=Depends(get_db),
    _=Depends(require_api_key),
):
    """
    Endpoint nội bộ: AI Core gửi kết quả nhận diện blacklist lên để lưu DB.
    Yêu cầu header X-API-Key hợp lệ.
    """
    row = await db.fetchrow(
        """INSERT INTO access_events
             (event_type, entity_type, entity_id, severity, camera_id, source_id, reason, json_path)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
           RETURNING id, event_type, entity_type, entity_id, severity,
                     camera_id, source_id, reason, event_timestamp::text,
                     alert_sent, json_path AS image_path""",
        body.event_type, body.entity_type, body.entity_id, body.severity,
        body.camera_id, body.source_id, body.reason, body.json_path,
    )
    event_dict = dict(row)
    _broadcast(event_dict)
    return {"id": row["id"], "event_timestamp": row["event_timestamp"]}


class RecognitionNotify(BaseModel):
    """Payload AI Core gửi sau khi ghi recognition_logs để trigger SSE push."""
    event_id:       str
    event_type:     str          # lpr_recognition | face_recognition | stranger_detected
    entity_type:    str          # plate | person
    entity_id:      Optional[str] = None
    severity:       str = "MEDIUM"
    camera_id:      Optional[str] = None
    source_id:      Optional[str] = None
    reason:         Optional[str] = None
    event_timestamp: str
    image_path:     Optional[str] = None


@router.post("/notify-recognition", status_code=200, summary="AI Core notify SSE sau khi ghi recognition_logs")
async def notify_recognition(
    body: RecognitionNotify,
    _=Depends(require_api_key),
):
    """
    AI Core gọi endpoint này sau mỗi lần ghi recognition_logs thành công.
    Backend sẽ broadcast event tới tất cả SSE clients đang kết nối.
    Không ghi DB — chỉ push SSE.
    """
    event_dict = {
        "id":              body.event_id,
        "event_type":      body.event_type,
        "entity_type":     body.entity_type,
        "entity_id":       body.entity_id,
        "severity":        body.severity,
        "camera_id":       body.camera_id,
        "source_id":       body.source_id,
        "reason":          body.reason,
        "event_timestamp": body.event_timestamp,
        "alert_sent":      False,
        "image_path":      body.image_path,
    }
    _broadcast(event_dict)
    return {"ok": True}
