"""
go2rtc REST client — gọi từ backend mỗi khi cameras bị thay đổi.

Thay thế service `go2rtc-sync` riêng. Backend là nơi:
  - Tạo/sửa/xoá camera (POST/PATCH/DELETE /api/cameras)
  - Biết source_id chuẩn (cùng logic _resolve_source_id ở routers/stream.py)
  → Gọi go2rtc REST trực tiếp ngay sau khi commit DB.

Failsafe: rtsp_ingest vẫn poll DB mỗi 30s và sync go2rtc khi startup,
nên nếu go2rtc-sync call ở backend fail (network, go2rtc not ready),
state sẽ được tái-đồng bộ sau ≤30s.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("go2rtc_client")

_GO2RTC_URL = os.environ.get("GO2RTC_URL", "http://svpro-go2rtc:1984")
_TIMEOUT = 5.0

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=_GO2RTC_URL, timeout=_TIMEOUT)
    return _client


def resolve_source_id(camera: dict) -> str:
    """source_id = name (nếu có) hoặc cam_{id}. Khớp với rtsp_ingest + PG NOTIFY trigger."""
    return camera.get("name") or f"cam_{camera['id']}"


async def add_or_update_stream(camera: dict) -> bool:
    """
    PUT /api/streams?name=<sid>&src=<rtsp_url>.
    Idempotent — go2rtc cập nhật nếu đã tồn tại.
    Skip nếu camera disabled hoặc không có rtsp_url.
    """
    if not camera.get("enabled") or not camera.get("rtsp_url"):
        return False
    sid = resolve_source_id(camera)
    try:
        client = _get_client()
        resp = await client.put(
            "/api/streams",
            params={"name": sid, "src": camera["rtsp_url"]},
        )
        if resp.status_code >= 300:
            logger.warning("go2rtc add stream %s failed: %s %s", sid, resp.status_code, resp.text[:200])
            return False
        logger.info("[go2rtc] added stream %s → %s", sid, camera["rtsp_url"][:60])
        return True
    except Exception as exc:
        logger.warning("go2rtc add stream %s exception: %s — fallback rtsp_ingest poll", sid, exc)
        return False


async def remove_stream(source_id: str) -> bool:
    """DELETE /api/streams?src=<sid>. Idempotent."""
    try:
        client = _get_client()
        resp = await client.delete("/api/streams", params={"src": source_id})
        if resp.status_code >= 400 and resp.status_code != 404:
            logger.warning("go2rtc remove stream %s failed: %s", source_id, resp.status_code)
            return False
        logger.info("[go2rtc] removed stream %s", source_id)
        return True
    except Exception as exc:
        logger.warning("go2rtc remove stream %s exception: %s", source_id, exc)
        return False


async def list_streams() -> Optional[dict]:
    """GET /api/streams. None nếu go2rtc không khả dụng."""
    try:
        client = _get_client()
        resp = await client.get("/api/streams")
        if resp.status_code != 200:
            return None
        return resp.json() or {}
    except Exception:
        return None


async def close():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
