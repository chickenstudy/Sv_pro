"""
Router streaming video — FastAPI backend SV-PRO.

Cung cấp stream video từ camera RTSP đến browser qua WebSocket.
Sử dụng FFmpeg subprocess để decode RTSP và forward MPEG-TS frame
sang WebSocket client (jsmpeg format).

Cách hoạt động:
  Browser (jsmpeg) → WebSocket /ws/stream/{camera_id}
  → Backend: FFmpeg decode RTSP → MPEG-TS → forward binary frames
  → Browser: jsmpeg player hiển thị video

Endpoints:
  WS /ws/stream/{camera_id}  — Video stream (binary MPEG-TS frames)
  GET /api/stream/{camera_id}/status — Trạng thái stream của camera
"""

import asyncio
import subprocess
import shlex
import logging
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from ..database import get_db
from .auth import require_jwt

router = APIRouter()
logger = logging.getLogger("stream")

# Cache camera config để tránh query DB mỗi frame
_camera_cache: dict[int, dict] = {}
_cache_ttl = 0
_CACHE_TTL_SEC = 30


async def _get_cached_camera(cam_id: int) -> Optional[dict]:
    """Lấy camera config từ cache hoặc DB."""
    global _camera_cache, _cache_ttl
    import time

    now = time.time()
    cached = _camera_cache.get(cam_id)
    if cached and _cache_ttl > now:
        return cached

    db = await get_db()
    row = await db.fetchrow(
        "SELECT id, name, rtsp_url, enabled FROM cameras WHERE id=$1", cam_id,
    )
    if row:
        _camera_cache[cam_id] = dict(row)
        _cache_ttl = now + _CACHE_TTL_SEC
        return _camera_cache[cam_id]
    return None


class StreamSession:
    """Quản lý 1 session stream FFmpeg → WebSocket."""

    def __init__(self, cam_id: int, rtsp_url: str):
        self.cam_id = cam_id
        self.rtsp_url = rtsp_url
        self.process: Optional[subprocess.Popen] = None
        self.ws_clients: set[WebSocket] = set()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self, ws: WebSocket):
        """Bắt đầu FFmpeg process và stream video."""
        await ws.accept()
        self.ws_clients.add(ws)
        self._running = True

        # FFmpeg: RTSP → MPEG-TS (jsmpeg compatible)
        # -re: read at native framerate
        # -c copy: copy codec (fast, no re-encode)
        # -f mpegts: output format MPEG-TS
        cmd = (
            f"ffmpeg -rtsp_transport tcp -re -i {shlex.quote(self.rtsp_url)} "
            f"-c copy -f mpegts - "
        )

        logger.info(f"[cam={self.cam_id}] Starting FFmpeg: {cmd}")

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=True,
            )
        except Exception as e:
            logger.error(f"[cam={self.cam_id}] FFmpeg start failed: {e}")
            await ws.close(code=1011, reason=f"FFmpeg failed: {e}")
            return

        self._task = asyncio.create_task(self._stream_loop())

    async def _stream_loop(self):
        """Đọc frame từ FFmpeg stdout và gửi qua WebSocket."""
        loop = asyncio.get_event_loop()
        BUF_SIZE = 8192

        try:
            while self._running and self.process and self.process.poll() is None:
                try:
                    data = await asyncio.wait_for(
                        loop.run_in_executor(None, self.process.stdout.read, BUF_SIZE),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    continue

                if not data:
                    await asyncio.sleep(0.1)
                    continue

                dead = set()
                for client in self.ws_clients:
                    try:
                        await client.send_bytes(data)
                    except Exception:
                        dead.add(client)

                for client in dead:
                    self.ws_clients.discard(client)

        except asyncio.CancelledError:
            pass
        finally:
            self._cleanup()

    def _cleanup(self):
        self._running = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                self.process.kill()
            self.process = None

    async def stop(self, ws: WebSocket):
        self.ws_clients.discard(ws)
        if not self.ws_clients:
            self._cleanup()


# Global registry: camera_id → StreamSession
_active_streams: dict[int, StreamSession] = {}


@router.websocket("/ws/stream/{cam_id}")
async def ws_video_stream(cam_id: int, websocket: WebSocket):
    """
    WebSocket endpoint streaming video.
    Browser kết nối: ws://host/ws/stream/{camera_id}
    Sau đó khởi tạo jsmpeg player: new JSMpeg.Player(url)
    """
    # Validate camera tồn tại
    cam = await _get_cached_camera(cam_id)
    if not cam:
        await websocket.close(code=1008, reason=f"Camera {cam_id} not found")
        return
    if not cam.get("enabled"):
        await websocket.close(code=1008, reason=f"Camera {cam_id} is disabled")
        return

    rtsp_url = cam.get("rtsp_url")
    if not rtsp_url or not rtsp_url.startswith("rtsp://"):
        await websocket.close(code=1008, reason=f"Camera {cam_id} has invalid RTSP URL")
        return

    # Dùng chung session cho tất cả client cùng camera
    if cam_id not in _active_streams:
        _active_streams[cam_id] = StreamSession(cam_id, rtsp_url)
        asyncio.create_task(_active_streams[cam_id].start(websocket))
    else:
        session = _active_streams[cam_id]
        if not session._running:
            # Restart stream mới
            asyncio.create_task(session.start(websocket))
        else:
            session.ws_clients.add(websocket)
            await websocket.accept()

    try:
        while True:
            # Chờ client disconnect signal
            await websocket.receive_text()
    except WebSocketDisconnect:
        session = _active_streams.get(cam_id)
        if session:
            await session.stop(websocket)
            if not session.ws_clients and session._running:
                session._cleanup()
                del _active_streams[cam_id]
    except Exception as e:
        logger.error(f"[cam={cam_id}] Stream error: {e}")
        session = _active_streams.get(cam_id)
        if session:
            await session.stop(websocket)
            if not session.ws_clients:
                session._cleanup()
                del _active_streams[cam_id]


@router.get("/stream/{cam_id}/status")
async def stream_status(cam_id: int, _=Depends(require_jwt)):
    """Trả về trạng thái stream của camera."""
    session = _active_streams.get(cam_id)
    return {
        "camera_id": cam_id,
        "streaming": session is not None and session._running,
        "clients": len(session.ws_clients) if session else 0,
        "ffmpeg_running": session.process is not None and session.process.poll() is None if session else False,
    }


@router.get("/stream/active")
async def list_active_streams(_=Depends(require_jwt)):
    """Liệt kê các camera đang stream."""
    return [
        {
            "camera_id": cam_id,
            "clients": len(s.ws_clients),
            "running": s._running,
        }
        for cam_id, s in _active_streams.items()
    ]
