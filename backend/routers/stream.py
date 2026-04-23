"""
Router streaming video — FastAPI backend SV-PRO (Refactored).

Trước: spawn FFmpeg subprocess → MPEG-TS → WebSocket (jsmpeg).
Sau:   proxy URL/token của go2rtc → Browser tự kết nối WebRTC/HLS trực tiếp.

Lý do thay đổi:
  - go2rtc xử lý RTSP → WebRTC/HLS/MSE hiệu quả hơn FFmpeg subprocess.
  - Browser kết nối trực tiếp go2rtc — không tốn tài nguyên backend.
  - Không cần spawn process, không cần quản lý pipe, không cần jsmpeg.

Endpoints:
  GET /api/stream/{cam_id}/info      — URL go2rtc cho camera (WebRTC + HLS + RTSP)
  GET /api/stream/{cam_id}/status    — Trạng thái stream từ go2rtc API
  GET /api/stream/active             — Danh sách streams đang active trên go2rtc
"""

import asyncio
import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status

from ..database import get_db
from .auth import require_jwt, require_jwt_query_or_header

router = APIRouter()
logger = logging.getLogger("stream")

# ── go2rtc config ─────────────────────────────────────────────────────────────
# Internal URL — backend gọi go2rtc bên trong Docker network
GO2RTC_URL      = os.environ.get("GO2RTC_URL", "http://svpro-go2rtc:1984")
GO2RTC_RTSP_URL = os.environ.get("GO2RTC_RTSP_URL", "rtsp://svpro-go2rtc:8554")

# Public URL — trả cho browser/máy khác kết nối trực tiếp go2rtc
# Fallback = GO2RTC_URL (cùng máy chủ, Docker internal network)
GO2RTC_PUBLIC_URL = os.environ.get("PUBLIC_GO2RTC_URL", GO2RTC_URL)

# HTTP client để gọi go2rtc API (luôn dùng internal URL)
_go2rtc_client = httpx.AsyncClient(base_url=GO2RTC_URL, timeout=5.0)

# Cache camera config để tránh query DB mỗi request
# {cam_id: (data_dict, expire_timestamp)}
_camera_cache: dict[int, tuple[dict, float]] = {}
_CACHE_TTL_SEC = 30


async def _get_cached_camera(cam_id: int, db) -> Optional[dict]:
    """Lấy camera config từ cache hoặc DB. Nhận db connection từ endpoint (Depends)."""
    import time
    global _camera_cache

    now = time.time()
    cached_entry = _camera_cache.get(cam_id)
    if cached_entry:
        data, expire_at = cached_entry
        if expire_at > now:
            return data

    row = await db.fetchrow(
        "SELECT id, name, rtsp_url, enabled FROM cameras WHERE id=$1", cam_id,
    )
    if not row:
        return None
    _camera_cache[cam_id] = (dict(row), now + _CACHE_TTL_SEC)
    return _camera_cache[cam_id][0]


def _build_stream_urls(source_id: str, public_base: str) -> dict:
    """
    Trả về các URL stream của go2rtc cho source_id đó.
    Browser có thể dùng WebRTC, HLS hoặc MSE tuỳ player.
    public_base: base URL dùng cho browser (PUBLIC_GO2RTC_URL).

    Các endpoint stream của go2rtc nằm dưới prefix /api/ — phải khớp đúng
    nếu không trình duyệt sẽ nhận 404.
    """
    return {
        # WebRTC: độ trễ thấp nhất (~200ms)
        "webrtc":     f"{public_base}/api/webrtc?src={source_id}",
        # HLS: tương thích rộng nhất (Safari, iOS)
        "hls":        f"{public_base}/api/stream.m3u8?src={source_id}",
        # MSE (Media Source Extensions): Chrome/Firefox, latency thấp hơn HLS
        "mse":        f"{public_base}/api/stream.mp4?src={source_id}",
        # RTSP re-stream: cho các client RTSP khác (VLC, Savant) — dùng internal
        "rtsp":       f"{GO2RTC_RTSP_URL}/{source_id}",
        # go2rtc player UI
        "player_ui":  f"{public_base}/?src={source_id}",
    }


def _resolve_source_id(cam: dict) -> str:
    """
    source_id phải khớp với COALESCE(name, 'cam_' || id::text) trong PG NOTIFY
    trigger (migration 002) và logic của go2rtc_sync.py / rtsp_ingest.py.
    Nếu không khớp → go2rtc không tìm thấy stream → frontend 404.
    """
    name = cam.get("name")
    return name if name else f"cam_{cam['id']}"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/api/stream/{cam_id}/info", summary="Stream URLs của camera")
async def stream_info(cam_id: int, db=Depends(get_db), _=Depends(require_jwt)):
    """
    Trả về các URL stream go2rtc cho camera.
    Frontend dùng URL này để kết nối WebRTC/HLS trực tiếp — không qua backend.
    """
    cam = await _get_cached_camera(cam_id, db)
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera {cam_id} không tồn tại")
    if not cam.get("enabled"):
        raise HTTPException(status_code=400, detail=f"Camera {cam_id} đang bị tắt")

    source_id = _resolve_source_id(cam)
    return {
        "camera_id": cam_id,
        "source_id": source_id,
        "urls":      _build_stream_urls(source_id, GO2RTC_PUBLIC_URL),
    }


@router.get("/api/stream/{cam_id}/status", summary="Trạng thái stream")
async def stream_status(cam_id: int, db=Depends(get_db), _=Depends(require_jwt)):
    """Trả về trạng thái stream của camera từ go2rtc API."""
    cam = await _get_cached_camera(cam_id, db)
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera {cam_id} không tồn tại")

    source_id = _resolve_source_id(cam)

    try:
        resp = await _go2rtc_client.get("/api/streams")
        resp.raise_for_status()
        streams: dict = resp.json() or {}
        stream_data = streams.get(source_id)
        is_active = stream_data is not None
        producers = stream_data.get("producers", []) if stream_data else []
        consumers = stream_data.get("consumers", []) if stream_data else []
    except Exception as exc:
        logger.warning("Cannot reach go2rtc API: %s", exc)
        is_active = False
        producers = []
        consumers = []

    return {
        "camera_id":   cam_id,
        "source_id":   source_id,
        "active":      is_active,
        "producers":   len(producers),   # Nguồn input (RTSP camera)
        "consumers":   len(consumers),   # Số client đang xem
        "urls":        _build_stream_urls(source_id, GO2RTC_PUBLIC_URL) if is_active else {},
    }


@router.get("/api/stream/{cam_id}/snapshot", summary="Lấy 1 frame JPEG từ camera")
async def stream_snapshot(cam_id: int, db=Depends(get_db), _=Depends(require_jwt_query_or_header)):
    """
    Trả về 1 frame JPEG hiện tại từ go2rtc — dùng cho ROI editor (FE vẽ polygon
    trên ảnh tĩnh thay vì stream live). Internal proxy → không expose go2rtc port.
    """
    from fastapi.responses import Response
    cam = await _get_cached_camera(cam_id, db)
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera {cam_id} không tồn tại")
    source_id = _resolve_source_id(cam)
    try:
        resp = await _go2rtc_client.get(
            f"/api/frame.jpeg",
            params={"src": source_id},
            timeout=8.0,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"go2rtc trả {resp.status_code}")
        return Response(content=resp.content, media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("snapshot fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="go2rtc không phản hồi")


@router.get("/api/stream/active", summary="Danh sách streams đang active")
async def list_active_streams(_=Depends(require_jwt)):
    """Liệt kê tất cả streams đang active trên go2rtc."""
    try:
        resp = await _go2rtc_client.get("/api/streams")
        resp.raise_for_status()
        streams: dict = resp.json() or {}
    except Exception as exc:
        logger.warning("Cannot reach go2rtc API: %s", exc)
        return {"error": "go2rtc unavailable", "streams": []}

    return {
        "total": len(streams),
        "streams": [
            {
                "source_id": name,
                "producers": len(data.get("producers", [])),
                "consumers": len(data.get("consumers", [])),
                "urls":      _build_stream_urls(name, GO2RTC_PUBLIC_URL),
            }
            for name, data in streams.items()
        ],
    }


# ── WebSocket: live detection overlay ─────────────────────────────────────────
#
# BlacklistEngine (Savant stage 3) publish detection metadata vào Redis channel
# `svpro:detections:{source_id}` mỗi frame. Endpoint này subscribe channel đó
# và forward message tới client qua WebSocket → FE vẽ bbox overlay lên video.
#
# Không dùng HTTP polling vì:
#   - Latency cao (min 100-200ms / request)
#   - Overhead kết nối mới mỗi lần
#   - FPS 8-12 → 10+ req/s / cam → backend load không cần thiết
# WebSocket: 1 kết nối, sub-ms latency, natural fit với pub/sub pattern.

import redis.asyncio as aioredis

_REDIS_URL = (
    f"redis://{os.environ.get('REDIS_HOST', 'redis')}:"
    f"{int(os.environ.get('REDIS_PORT', '6379'))}"
    f"/{int(os.environ.get('REDIS_DB', '0'))}"
)


@router.websocket("/api/stream/detections/{source_id}")
async def ws_detections(websocket: WebSocket, source_id: str, t: str | None = None):
    """
    WebSocket stream detection metadata cho 1 camera.

    Auth: query param `?t=<jwt>` (WS không support Authorization header
    trong browser gốc). Token verify giống REST endpoints.

    Protocol:
      - Server → Client: JSON message mỗi khi có detection mới.
        {ts, source_id, frame_w, frame_h, detections: [{bbox, label, ...}, ...]}
      - Client → Server: không gửi gì (read-only stream).
    """
    # Verify JWT từ query string
    from .auth import _verify_token
    if not t or not _verify_token(t):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid token")
        return

    await websocket.accept()

    r = aioredis.from_url(_REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    channel = f"svpro:detections:{source_id}"

    try:
        await pubsub.subscribe(channel)
        logger.info("WS subscribed %s client=%s", channel, websocket.client)

        while True:
            # listen với timeout để có thể phát hiện WS disconnect
            try:
                msg = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                msg = None

            if msg and msg.get("type") == "message":
                try:
                    await websocket.send_text(msg["data"])
                except (WebSocketDisconnect, RuntimeError):
                    break

            # Heartbeat check: nếu client đã disconnect thì exit loop
            try:
                # receive_text với timeout ngắn chỉ để detect close frame
                await asyncio.wait_for(websocket.receive_text(), timeout=0.001)
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WS detections error (%s): %s", channel, exc)
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            await r.aclose()
        except Exception:
            pass
        logger.info("WS disconnected %s", channel)
