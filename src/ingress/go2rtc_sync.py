"""
go2rtc Camera Sync — SV-PRO.

Thay thế ingress_manager.py (~391 LOC) + eos_guard.py (~279 LOC).

Chức năng:
  - Đọc cameras từ PostgreSQL khi startup.
  - Sync camera list → go2rtc REST API (/api/streams).
  - Lắng nghe PostgreSQL LISTEN/NOTIFY để cập nhật ngay khi DB thay đổi.
  - Fallback poll 30s nếu NOTIFY không khả dụng.

go2rtc tự xử lý:
  - RTSP reconnect khi camera drop (không cần EOS guard thủ công).
  - Expose RTSP re-stream tại rtsp://svpro-go2rtc:8554/{source_id} cho Savant.
  - Expose WebRTC/HLS tại http://svpro-go2rtc:1984 cho browser.

Khởi chạy:
  python -m src.ingress.go2rtc_sync
"""

import json
import logging
import os
import select
import signal
import sys
import time
from dataclasses import dataclass

import httpx
import psycopg2
import psycopg2.extensions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("go2rtc-sync")

# ── Config từ env ──────────────────────────────────────────────────────────────
DB_DSN        = os.environ.get("POSTGRES_DSN", "postgresql://svpro_user:svpro_pass@postgres:5432/svpro_db")
GO2RTC_URL    = os.environ.get("GO2RTC_URL", "http://svpro-go2rtc:1984")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECS", "30"))
CHANNEL       = "cameras_changed"

_running = True


def _sighandler(sig, _frame):
    """Xử lý SIGINT/SIGTERM để shutdown graceful."""
    global _running
    log.info("Received signal %s — shutting down.", sig)
    _running = False


signal.signal(signal.SIGINT, _sighandler)
if sys.platform != "win32":
    signal.signal(signal.SIGTERM, _sighandler)


@dataclass(frozen=True)
class CameraRow:
    """Snapshot bất biến của 1 camera row từ DB."""
    id:        int
    name:      str
    rtsp_url:  str
    source_id: str
    enabled:   bool

    @staticmethod
    def make(row: tuple) -> "CameraRow":
        """Build from (id, name, rtsp_url, enabled) tuple."""
        row_id, row_name, row_rtsp, row_enabled = row
        sid = f"cam_{row_id}"
        return CameraRow(id=row_id, name=row_name, rtsp_url=row_rtsp, source_id=sid, enabled=row_enabled)


def fetch_cameras(dsn: str) -> list[CameraRow]:
    """Lấy danh sách camera enabled từ PostgreSQL.

    source_id luôn là 'cam_{id}' — đảm bảo mỗi camera có stream name DUY NHẤT,
    bất kể name có trùng nhau hay không.
    """
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, rtsp_url, enabled "
            "FROM cameras ORDER BY id"
        )
        rows = [CameraRow.make(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as exc:
        log.error("DB fetch_cameras failed: %s", exc)
        return []


def sync_to_go2rtc(cameras: list[CameraRow], client: httpx.Client) -> None:
    """
    Đồng bộ danh sách camera với go2rtc qua REST API.

    go2rtc API:
      PUT    /api/streams?name={source_id}  body=rtsp_url  → thêm/cập nhật stream
      DELETE /api/streams?src={source_id}                 → xóa stream
    """
    try:
        resp = client.get(f"{GO2RTC_URL}/api/streams")
        resp.raise_for_status()
        existing: dict = resp.json() or {}
    except Exception as exc:
        log.error("Cannot reach go2rtc API: %s", exc)
        return

    desired_ids = set()

    for cam in cameras:
            if not cam.enabled or not cam.rtsp_url:
                continue
            desired_ids.add(cam.source_id)
            try:
                resp = client.put(
                    f"{GO2RTC_URL}/api/streams",
                    params={"name": cam.source_id},
                    content=cam.rtsp_url.encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                resp.raise_for_status()
                log.info("✅ Synced [%s] → %s", cam.source_id, cam.rtsp_url[:60])
            except Exception as exc:
                log.error("Failed to add stream [%s]: %s", cam.source_id, exc)

    # Xóa streams không còn trong DB hoặc bị disabled
    for stream_name in list(existing.keys()):
        if stream_name not in desired_ids:
            try:
                client.delete(
                    f"{GO2RTC_URL}/api/streams",
                    params={"src": stream_name},
                ).raise_for_status()
                log.info("🗑️  Removed [%s] from go2rtc", stream_name)
            except Exception as exc:
                log.warning("Failed to remove [%s]: %s", stream_name, exc)

    log.info("Sync done: %d active streams.", len(desired_ids))


def wait_for_go2rtc(client: httpx.Client, max_retries: int = 30) -> bool:
    """Chờ go2rtc sẵn sàng trước khi sync."""
    for attempt in range(max_retries):
        try:
            resp = client.get(f"{GO2RTC_URL}/api/streams", timeout=3.0)
            if resp.status_code < 500:
                log.info("go2rtc is ready.")
                return True
        except Exception:
            pass
        log.info("Waiting for go2rtc... (%d/%d)", attempt + 1, max_retries)
        time.sleep(2)
    return False


def listen_loop(dsn: str, client: httpx.Client) -> None:
    """
    Main loop: LISTEN PostgreSQL NOTIFY + fallback poll.

    Camera thay đổi trong DB → trigger gửi NOTIFY
    → go2rtc cập nhật ngay lập tức (< 100ms).
    Fallback: poll POLL_INTERVAL giây để đảm bảo không bỏ sót.
    """
    conn: psycopg2.extensions.connection | None = None

    while _running:
        # Kết nối và đăng ký LISTEN
        if conn is None or conn.closed:
            try:
                conn = psycopg2.connect(dsn)
                conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
                cur = conn.cursor()
                cur.execute(f"LISTEN {CHANNEL}")
                cur.close()
                log.info("PG LISTEN registered on channel '%s'", CHANNEL)
                # Full sync khi mới kết nối
                sync_to_go2rtc(fetch_cameras(dsn), client)
            except Exception as exc:
                log.error("PG LISTEN setup failed: %s — retrying in 5s", exc)
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None
                time.sleep(5)
                continue

        try:
            # Chờ NOTIFY hoặc timeout POLL_INTERVAL giây
            if select.select([conn], [], [], POLL_INTERVAL) == ([], [], []):
                # Timeout: full sync định kỳ
                sync_to_go2rtc(fetch_cameras(dsn), client)
            else:
                conn.poll()
                while conn.notifies:
                    notify = conn.notifies.pop(0)
                    log.info("PG NOTIFY on '%s'", notify.channel)
                    try:
                        payload = json.loads(notify.payload)
                        log.info("Camera changed: op=%s id=%s", payload.get("op"), payload.get("id"))
                    except Exception:
                        pass
                    sync_to_go2rtc(fetch_cameras(dsn), client)

        except Exception as exc:
            log.error("PG LISTEN loop error: %s — reconnecting", exc)
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
            time.sleep(3)

    if conn and not conn.closed:
        conn.close()
    log.info("go2rtc sync shutdown complete.")


def main():
    log.info("═══ SV-PRO go2rtc Sync started ═══")
    log.info("DB : %s", DB_DSN.split("@")[-1])
    log.info("go2rtc: %s | Poll fallback: %ds", GO2RTC_URL, POLL_INTERVAL)

    client = httpx.Client(timeout=10.0)

    if not wait_for_go2rtc(client):
        log.error("go2rtc not available after retries — exiting.")
        sys.exit(1)

    try:
        listen_loop(DB_DSN, client)
    finally:
        client.close()
        log.info("═══ go2rtc Sync stopped ═══")


if __name__ == "__main__":
    main()
