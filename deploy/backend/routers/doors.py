"""
Router Doors — FastAPI backend SV-PRO Sprint 4 Task 4.4b.

Endpoints:
  POST /api/doors/{door_id}/trigger   — Kích hoạt mở cửa (FR pipeline hoặc operator)
  GET  /api/doors                     — Danh sách cửa đã cấu hình
  GET  /api/doors/{door_id}           — Thông tin 1 cửa
  PATCH /api/doors/{door_id}/toggle   — Bật/tắt cửa

Yêu cầu xác thực:
  - POST /trigger: API Key (AI Core internal)
  - Các endpoint khác: JWT (Dashboard)
"""

import os
import asyncio
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import aiohttp

from ..database import get_db
from .auth import require_jwt, require_api_key

_VN_TZ = timezone(timedelta(hours=7))

router = APIRouter()

# ── Cấu hình cửa từ biến môi trường (fallback nếu không có YAML) ──────────────
# Đọc danh sách cửa từ env DOOR_CONFIG_JSON (JSON array)
# Ví dụ: '[{"door_id":"gate_main","name":"Cổng chính","relay_url":"http://192.168.1.10/relay/1","zone":"main_gate"}]'
_DEFAULT_DOORS = [
    {
        "door_id":   "gate_main",
        "name":      "Cổng chính",
        "relay_url": os.environ.get("RELAY_GATE_MAIN", "http://192.168.1.10/relay/1"),
        "zone":      "main_gate",
        "open_ms":   5000,
        "enabled":   True,
    },
    {
        "door_id":   "door_server_room",
        "name":      "Phòng máy chủ",
        "relay_url": os.environ.get("RELAY_SERVER_ROOM", "http://192.168.1.11/relay/1"),
        "zone":      "server_room",
        "open_ms":   3000,
        "enabled":   True,
    },
]


# ── Pydantic schemas ────────────────────────────────────────────────────────────

class TriggerRequest(BaseModel):
    """Payload từ AI Core khi yêu cầu mở cửa sau khi FR pass."""
    person_id:    str
    person_name:  str
    person_role:  str
    camera_id:    str
    source_id:    str
    liveness_ok:  bool = True
    zone_allowed: bool = True
    fr_confidence: float = 0.0


class TriggerResponse(BaseModel):
    """Kết quả trigger mở cửa."""
    door_id:    str
    granted:    bool
    reason:     str
    timestamp:  str
    latency_ms: float


class DoorOut(BaseModel):
    """Thông tin một cửa."""
    door_id:   str
    name:      str
    zone:      str
    enabled:   bool
    relay_url: str
    open_ms:   int


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _get_doors() -> list[dict]:
    """Lấy danh sách cửa từ env hoặc default config."""
    raw = os.environ.get("DOOR_CONFIG_JSON")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return _DEFAULT_DOORS


def _find_door(door_id: str) -> Optional[dict]:
    """Tìm config cửa theo door_id."""
    for d in _get_doors():
        if d["door_id"] == door_id:
            return d
    return None


# ── Endpoints ───────────────────────────────────────────────────────────────────

@router.get("", response_model=list[DoorOut], summary="Danh sách cửa")
async def list_doors(_=Depends(require_jwt)):
    """
    Trả về danh sách tất cả cửa đã cấu hình trong hệ thống.
    Yêu cầu JWT token.
    """
    return [
        DoorOut(
            door_id   = d["door_id"],
            name      = d.get("name", d["door_id"]),
            zone      = d.get("zone", ""),
            enabled   = d.get("enabled", True),
            relay_url = d.get("relay_url", ""),
            open_ms   = d.get("open_ms", 5000),
        )
        for d in _get_doors()
    ]


@router.get("/{door_id}", response_model=DoorOut, summary="Thông tin 1 cửa")
async def get_door(door_id: str, _=Depends(require_jwt)):
    """Chi tiết một cửa theo door_id."""
    door = _find_door(door_id)
    if not door:
        raise HTTPException(status_code=404, detail=f"Cửa '{door_id}' không tồn tại")
    return DoorOut(
        door_id   = door["door_id"],
        name      = door.get("name", door_id),
        zone      = door.get("zone", ""),
        enabled   = door.get("enabled", True),
        relay_url = door.get("relay_url", ""),
        open_ms   = door.get("open_ms", 5000),
    )


@router.post("/{door_id}/trigger", response_model=TriggerResponse, summary="Kích hoạt mở cửa")
async def trigger_door(
    door_id: str,
    body:    TriggerRequest,
    db=Depends(get_db),
    _=Depends(require_api_key),   # Chỉ AI Core nội bộ
):
    """
    Endpoint nội bộ — AI Core gửi yêu cầu mở cửa sau khi FR pass.

    Flow:
      1. Kiểm tra door_id tồn tại.
      2. Gọi HTTP relay để mở cửa vật lý.
      3. Ghi sự kiện vào bảng access_events (audit log).
      4. Trả về kết quả.

    Yêu cầu header: X-API-Key hợp lệ.
    """
    door = _find_door(door_id)
    if not door:
        raise HTTPException(status_code=404, detail=f"Cửa '{door_id}' không tồn tại")

    if not door.get("enabled", True):
        await _log_access_event(db, door_id, body, granted=False,
                                reason="Cửa đang bị vô hiệu hóa")
        return TriggerResponse(
            door_id    = door_id,
            granted    = False,
            reason     = "Cửa đang bị vô hiệu hóa",
            timestamp  = datetime.now(_VN_TZ).isoformat(),
            latency_ms = 0.0,
        )

    if not body.liveness_ok:
        reason = "Phát hiện giả mạo khuôn mặt — từ chối truy cập"
        await _log_access_event(db, door_id, body, granted=False, reason=reason)
        return TriggerResponse(
            door_id    = door_id,
            granted    = False,
            reason     = reason,
            timestamp  = datetime.now(_VN_TZ).isoformat(),
            latency_ms = 0.0,
        )

    if not body.zone_allowed:
        reason = f"Role '{body.person_role}' không có quyền vào zone này"
        await _log_access_event(db, door_id, body, granted=False, reason=reason)
        return TriggerResponse(
            door_id    = door_id,
            granted    = False,
            reason     = reason,
            timestamp  = datetime.now(_VN_TZ).isoformat(),
            latency_ms = 0.0,
        )

    # Gửi lệnh HTTP relay
    latency_ms, success, msg = await _send_relay_http(door)
    ts = datetime.now(_VN_TZ).isoformat()

    reason = "Mở cửa thành công" if success else f"Relay lỗi: {msg}"
    await _log_access_event(db, door_id, body, granted=success, reason=reason,
                            latency_ms=latency_ms)

    return TriggerResponse(
        door_id    = door_id,
        granted    = success,
        reason     = reason,
        timestamp  = ts,
        latency_ms = latency_ms,
    )


@router.patch("/{door_id}/toggle", summary="Bật/tắt cửa")
async def toggle_door(door_id: str, enabled: bool, _=Depends(require_jwt)):
    """
    Bật hoặc tắt cửa tạm thời (ví dụ: bảo trì relay).
    Thay đổi chỉ có hiệu lực trong runtime — khởi động lại sẽ reset về config.
    """
    door = _find_door(door_id)
    if not door:
        raise HTTPException(status_code=404, detail=f"Cửa '{door_id}' không tồn tại")

    # Cập nhật trực tiếp trong list của _DEFAULT_DOORS (runtime only)
    for d in _DEFAULT_DOORS:
        if d["door_id"] == door_id:
            d["enabled"] = enabled
            break

    return {"door_id": door_id, "enabled": enabled, "message": f"Cửa đã {'bật' if enabled else 'tắt'}"}


# ── Internal helpers ────────────────────────────────────────────────────────────

async def _send_relay_http(door: dict) -> tuple[float, bool, str]:
    """
    Gửi HTTP POST tới relay controller để mở cửa vật lý.
    Chạy bất đồng bộ để không block event loop FastAPI.
    Trả về (latency_ms, success, message).
    """
    relay_url = door.get("relay_url", "")
    open_ms   = door.get("open_ms", 5000)
    payload   = {"action": "open", "duration_ms": open_ms, "door_id": door["door_id"]}

    start = asyncio.get_running_loop().time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(relay_url, json=payload, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                latency_ms = (asyncio.get_running_loop().time() - start) * 1000
                if 200 <= resp.status < 300:
                    return latency_ms, True, "OK"
                return latency_ms, False, f"HTTP {resp.status}"
    except Exception as exc:
        latency_ms = (asyncio.get_running_loop().time() - start) * 1000
        return latency_ms, False, str(exc)


async def _log_access_event(
    db,
    door_id:    str,
    body:       TriggerRequest,
    granted:    bool,
    reason:     str,
    latency_ms: float = 0.0,
) -> None:
    """
    Ghi sự kiện mở cửa vào bảng access_events để audit.
    Không raise exception — lỗi DB không được block trigger response.
    """
    import json, logging
    try:
        extra = json.dumps({
            "door_id":      door_id,
            "fr_confidence": body.fr_confidence,
            "liveness_ok":  body.liveness_ok,
            "zone_allowed": body.zone_allowed,
            "latency_ms":   round(latency_ms, 2),
        })
        await db.execute(
            """INSERT INTO access_events
               (event_type, entity_type, entity_id, severity, camera_id, source_id, reason, json_path)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            "door_trigger",
            "person",
            body.person_id,
            "LOW" if granted else "HIGH",
            body.camera_id,
            body.source_id,
            reason,
            extra,
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("Failed to log door event to DB: %s", exc)
