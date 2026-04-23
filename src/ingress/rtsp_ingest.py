"""
RTSP Ingest — SV-PRO (go2rtc → Savant ZMQ Bridge).

Chức năng:
  - Truy vấn go2rtc /api/streams để lấy danh sách camera active.
  - Với mỗi camera: kéo RTSP từ svpro-go2rtc (rtsp://svpro-go2rtc:8554/{source_id}).
  - Encode frame → push vào Savant AI Core qua ZMQ PUB socket.
  - Lắng nghe PG NOTIFY để cập nhật ngay khi camera thay đổi.
  - Fallback poll mỗi POLL_INTERVAL giây.

Kiến trúc video flow:
  Camera RTSP (IP) → go2rtc :8554 → [rtsp_ingest.py] → ZMQ IPC → savant-ai-core

Tại sao dùng ZMQ thay vì Savant pull RTSP trực tiếp:
  Savant AI Core (DeepStream) yêu cầu ZMQ làm input protocol cho multi-stream.
  Service này là "Savant Video Ingress" phía Python — đọc RTSP từ go2rtc broker
  và chuẩn hóa frame sang Savant's ZMQ message format.

Khởi chạy:
  python -m src.ingress.rtsp_ingest
"""

import json
import logging
import os

# Bắt buộc OpenCV dùng TCP để kéo luồng RTSP (tránh rơi packet dẫn tới lỗi giải mã H264)
# (Phải đặt TRƯỚC import cv2)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
import select
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import httpx
import psycopg2
import psycopg2.extensions

# savant_rs phải có sẵn trong image (Dockerfile.savant-ai-core).
# Import sớm để lỗi xuất hiện ngay khi startup, không phải lúc push frame đầu tiên.
try:
    from savant_rs.primitives import (
        VideoFrame,
        VideoFrameContent,
        VideoFrameTranscodingMethod,
    )
    from savant_rs.utils.serialization import Message as SavantMessage
    from savant_rs.utils.serialization import save_message
    _SAVANT_RS_OK = True
except ImportError as _savant_err:
    _SAVANT_RS_OK = False
    # Log lỗi sau khi logging được khởi tạo (xem phần cuối module)
    _SAVANT_RS_ERR = _savant_err

logging.basicConfig(
    level=logging.INFO,   # Giảm từ DEBUG → INFO; giảm ~30% CPU format log
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("rtsp-ingest")

if not _SAVANT_RS_OK:
    log.critical(
        "savant_rs không tìm thấy: %s\n"
        "  → Container savant-rtsp-ingress phải dùng Dockerfile.savant-ai-core "
        "(không phải Dockerfile.backend).\n"
        "  → Chạy: docker compose build savant-rtsp-ingress",
        _SAVANT_RS_ERR,
    )
    sys.exit(1)

# ── Config từ env ──────────────────────────────────────────────────────────────

GO2RTC_URL        = os.environ.get("GO2RTC_URL", "http://svpro-go2rtc:1984")
GO2RTC_RTSP_BASE  = os.environ.get("GO2RTC_RTSP_BASE", "rtsp://svpro-go2rtc:8554")
ZMQ_PUB_ENDPOINT  = os.environ.get("ZMQ_PUB_ENDPOINT", "pub+connect:ipc:///tmp/zmq-sockets/input-video.ipc")
DB_DSN            = os.environ.get("POSTGRES_DSN", "postgresql://svpro_user:svpro_pass@postgres:5432/svpro_db")
POLL_INTERVAL     = int(os.environ.get("POLL_INTERVAL_SECS", "30"))
TARGET_FPS        = int(os.environ.get("TARGET_FPS", "10"))        # FPS gửi vào Savant
CHANNEL           = "cameras_changed"

_running = True

# PTS (presentation timestamp) tracking per source_id.
# Đơn vị: microseconds (time_base = 1/1_000_000).
# Mỗi frame tăng thêm 1_000_000 / TARGET_FPS µs.
_pts_counters: dict[str, int] = {}
_PTS_TIME_BASE = (1, 1_000_000)  # 1 µs per unit

# ZMQ socket KHÔNG thread-safe — nhiều StreamWorker thread gọi send_multipart()
# đồng thời trên cùng 1 socket → message interleaving / segfault.
# Lock này serialize tất cả ZMQ writes về một thread tại một thời điểm.
_zmq_lock = threading.Lock()


def _sighandler(sig, _frame):
    """Xử lý SIGINT/SIGTERM để shutdown graceful."""
    global _running
    log.info("Received signal %s — shutting down.", sig)
    _running = False


signal.signal(signal.SIGINT, _sighandler)
if sys.platform != "win32":
    signal.signal(signal.SIGTERM, _sighandler)


@dataclass
class StreamConfig:
    """Cấu hình của một camera stream."""
    source_id: str
    rtsp_url:  str   # URL RTSP từ go2rtc re-stream


@dataclass
class StreamWorker:
    """Worker thread đọc RTSP từ go2rtc và push ZMQ vào Savant."""
    config:     StreamConfig
    _thread:    Optional[threading.Thread] = field(default=None, repr=False)
    _stop_event: threading.Event            = field(default_factory=threading.Event, repr=False)

    def start(self, zmq_publisher) -> None:
        """Khởi động worker thread đọc RTSP và push ZMQ."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(zmq_publisher,),
            daemon=True,
            name=f"rtsp-{self.config.source_id}",
        )
        self._thread.start()
        log.info("▶ Stream worker started: [%s] %s", self.config.source_id, self.config.rtsp_url)

    def stop(self) -> None:
        """Dừng worker thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        log.info("⏹ Stream worker stopped: [%s]", self.config.source_id)

    def _run(self, zmq_publisher) -> None:
        """
        Vòng lặp đọc frame từ RTSP go2rtc và push vào ZMQ.

        Camera offline xử lý mềm:
          - Open RTSP fail → backoff dần 5→10→20→max 30s, log thưa (lần đầu
            + mỗi 5 lần fail tiếp theo) để khỏi spam log.
          - Khi cam online lại → log INFO "reconnected" rồi continue bình thường.
          - Worker KHÔNG bao giờ tự exit — luôn loop chờ cam quay lại,
            cho đến khi StreamManager.sync() thấy cam bị disabled/xóa khỏi DB
            và gọi stop().
        """
        frame_interval = 1.0 / max(TARGET_FPS, 1)
        last_frame_time = 0.0
        cap: Optional[cv2.VideoCapture] = None
        fail_count = 0
        read_fail_count = 0

        while not self._stop_event.is_set():
            # Kết nối RTSP nếu chưa có hoặc bị mất
            if cap is None or not cap.isOpened():
                rtsp_url = f"{GO2RTC_RTSP_BASE}/{self.config.source_id}"
                cap = cv2.VideoCapture(rtsp_url)
                if not cap.isOpened():
                    fail_count += 1
                    backoff = min(5 * fail_count, 30)
                    if fail_count == 1 or fail_count % 5 == 0:
                        log.warning(
                            "[%s] RTSP unreachable (fail #%d) — retry in %ds",
                            self.config.source_id, fail_count, backoff,
                        )
                    cap = None
                    if self._stop_event.wait(backoff):
                        break
                    continue
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if fail_count > 0:
                    log.info("✅ [%s] RTSP RECONNECTED after %d fail(s)",
                             self.config.source_id, fail_count)
                else:
                    log.info("✅ Connected: [%s]", self.config.source_id)
                fail_count = 0
                read_fail_count = 0

            # Throttle FPS để không gửi quá nhanh vào DeepStream
            now = time.monotonic()
            elapsed = now - last_frame_time
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)

            ret, frame = cap.read()
            if not ret or frame is None:
                read_fail_count += 1
                if read_fail_count == 1 or read_fail_count % 30 == 0:
                    log.warning(
                        "[%s] Frame read failed × %d — reconnecting.",
                        self.config.source_id, read_fail_count,
                    )
                cap.release()
                cap = None
                # Sleep ngắn cho read-fail (cam có thể chỉ glitch tạm thời)
                if self._stop_event.wait(2):
                    break
                continue

            last_frame_time = time.monotonic()
            read_fail_count = 0

            # Push frame vào ZMQ cho Savant
            _push_frame_zmq(zmq_publisher, self.config.source_id, frame)

        if cap:
            cap.release()


def _push_frame_zmq(publisher, source_id: str, frame) -> None:
    """
    Encode frame thành Savant RS VideoFrame protobuf và gửi qua ZMQ PUB.

    Savant AI Core dùng savant_rs để decode ZMQ message — nó kỳ vọng:
      multipart[0] = source_id (ZMQ topic, dùng để filter)
      multipart[1] = Message.serialize() (protobuf VideoFrame)

    KHÔNG được gửi raw JPEG bytes vì protobuf decoder sẽ fail với
    "invalid wire type value: 7" (0xFF = field 31, wire type 7 không tồn tại).
    """
    if publisher is None:
        return
    try:
        h, w = frame.shape[:2]

        # Encode JPEG (quality 85 — đủ cho AI, tiết kiệm băng thông ZMQ)
        # JPEG quality 92 (tăng từ 85): giữ chi tiết face tốt hơn, không bị
        # artifact blocking. CPU encode chỉ tăng ~10%, trade-off tốt cho
        # ảnh face save cuối cùng rõ nét hơn (chain lossy JPEG 92 + 95 < 85+95).
        ok, jpeg_buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok or jpeg_buf is None:
            log.warning("[%s] cv2.imencode failed — frame skipped", source_id)
            return
        jpeg_bytes = jpeg_buf.tobytes()

        # PTS tăng đơn điệu per source_id (µs)
        pts_step = int(_PTS_TIME_BASE[1] / max(TARGET_FPS, 1))
        pts = _pts_counters.get(source_id, 0)
        _pts_counters[source_id] = pts + pts_step
        
        frame_cnt = pts // pts_step
        if frame_cnt % 50 == 0:
            log.info("[%s] Successfully PUSHED %d frames to ZMQ socket", source_id, frame_cnt)

        # Tạo Savant RS VideoFrame
        vf = VideoFrame(
            source_id=source_id,
            framerate=f"{TARGET_FPS}/1",
            width=w,
            height=h,
            content=VideoFrameContent.internal(jpeg_bytes),
            transcoding_method=VideoFrameTranscodingMethod.Copy,
            codec="jpeg",
            keyframe=True,    # mỗi JPEG là independent frame
            pts=pts,
            dts=None,
            duration=None,
            time_base=_PTS_TIME_BASE,
        )

        # Serialize → protobuf bytes → gửi ZMQ multipart
        # Lock bắt buộc: ZMQ socket không thread-safe, mỗi StreamWorker
        # chạy trên thread riêng và gọi hàm này đồng thời.
        log.debug("[%s] Serializing SavantMessage", source_id)
        msg = SavantMessage.video_frame(vf)
        data = save_message(msg)
        with _zmq_lock:
            log.debug("[%s] Sending ZMQ multipart", source_id)
            publisher.send_multipart([source_id.encode(), data])
            log.debug("[%s] Finished ZMQ send", source_id)

    except Exception as exc:
        log.debug("[%s] ZMQ push error: %s", source_id, exc)


# ── Stream Manager ─────────────────────────────────────────────────────────────

class StreamManager:
    """
    Quản lý tập hợp StreamWorker theo danh sách camera active từ go2rtc.

    Khi camera được thêm → tạo worker mới.
    Khi camera bị xóa → dừng worker tương ứng.
    """

    def __init__(self, zmq_publisher):
        self._zmq_publisher = zmq_publisher
        self._workers: dict[str, StreamWorker] = {}
        self._lock = threading.Lock()

    def sync(self, active_streams: dict[str, str]) -> None:
        """
        Đồng bộ workers với danh sách stream active từ go2rtc.

        active_streams: {source_id: rtsp_url}
        """
        with self._lock:
            current = set(self._workers.keys())
            desired = set(active_streams.keys())

            # Thêm worker cho stream mới
            for sid in desired - current:
                cfg = StreamConfig(source_id=sid, rtsp_url=active_streams[sid])
                worker = StreamWorker(config=cfg)
                worker.start(self._zmq_publisher)
                self._workers[sid] = worker
                log.info("📹 Worker added for stream: [%s]", sid)

            # Dừng worker cho stream đã xóa
            for sid in current - desired:
                log.info("📹 Worker removed for stream: [%s]", sid)
                self._workers[sid].stop()
                del self._workers[sid]

        log.info("Streams: %d active | added=%d removed=%d | streams=%s",
                 len(desired), len(desired - current), len(current - desired),
                 list(desired))

    def stop_all(self) -> None:
        """Dừng tất cả worker khi shutdown."""
        with self._lock:
            for worker in self._workers.values():
                worker.stop()
            self._workers.clear()


# ── go2rtc stream discovery ────────────────────────────────────────────────────

# Shared HTTP client for go2rtc REST sync (reuse connection pool)
_go2rtc_client: httpx.Client | None = None


def _get_go2rtc_client() -> httpx.Client:
    global _go2rtc_client
    if _go2rtc_client is None:
        _go2rtc_client = httpx.Client(timeout=5.0)
    return _go2rtc_client


def _sync_streams_to_go2rtc(cameras: list[tuple]) -> None:
    """
    Đồng bộ trạng thái go2rtc với danh sách camera DB:
      - PUT (idempotent) mọi camera enabled vào go2rtc — kể cả cam offline
        (go2rtc tự retry connection ngầm).
      - DELETE stream tồn tại trong go2rtc nhưng không còn trong DB.

    Đây là nguồn-sự-thật-cuối-cùng từ DB → go2rtc, chạy mỗi lần fetch DB.
    Đảm bảo dù cam được insert trực tiếp DB hay backend call go2rtc fail,
    state vẫn convergent sau ≤POLL_INTERVAL giây.
    """
    client = _get_go2rtc_client()
    try:
        resp = client.get(f"{GO2RTC_URL}/api/streams")
        existing: dict = resp.json() or {} if resp.status_code == 200 else {}
    except Exception as exc:
        log.warning("go2rtc list streams failed (sync skipped): %s", exc)
        return

    desired_ids: set[str] = set()
    for row_id, row_name, row_rtsp, _enabled in cameras:
        if not row_rtsp:
            continue
        sid = row_name if row_name else f"cam_{row_id}"
        desired_ids.add(sid)
        try:
            resp = client.put(
                f"{GO2RTC_URL}/api/streams",
                params={"name": sid, "src": row_rtsp},
            )
            if resp.status_code >= 300:
                log.warning("go2rtc PUT %s failed: HTTP %s %s",
                            sid, resp.status_code, resp.text[:120])
            elif sid not in existing:
                log.info("[go2rtc] +stream %s → %s", sid, row_rtsp[:60])
        except Exception as exc:
            log.warning("go2rtc PUT %s exception: %s", sid, exc)

    # Cleanup: stream lạ trong go2rtc (vd cam đã xóa khỏi DB)
    for sid in list(existing.keys()):
        if sid not in desired_ids:
            try:
                client.delete(f"{GO2RTC_URL}/api/streams", params={"src": sid})
                log.info("[go2rtc] -stream %s (not in DB)", sid)
            except Exception as exc:
                log.debug("go2rtc DELETE %s exception: %s", sid, exc)


def fetch_active_streams() -> dict[str, str]:
    """
    Lấy cameras enabled từ DB → ĐỒNG BỘ vào go2rtc → trả map cho ingest.

    Trả về {source_id: rtsp://svpro-go2rtc:8554/{source_id}}.

    source_id = camera.name nếu có, ngược lại 'cam_{id}' — khớp với
    PG NOTIFY trigger COALESCE(name, 'cam_' || id::text).
    """
    try:
        conn = psycopg2.connect(DB_DSN)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, rtsp_url, enabled "
            "FROM cameras WHERE enabled = true ORDER BY id"
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as exc:
        log.error("DB fetch_cameras failed: %s", exc)
        return {}

    # Sync DB → go2rtc (PUT new, DELETE orphans). Camera offline vẫn PUT.
    _sync_streams_to_go2rtc(rows)

    result: dict[str, str] = {}
    for row_id, row_name, _rtsp, _enabled in rows:
        sid = row_name if row_name else f"cam_{row_id}"
        result[sid] = f"{GO2RTC_RTSP_BASE}/{sid}"
    if result:
        log.info("DB cameras for ingest: %s", list(result.keys()))
    else:
        log.warning("No enabled cameras found in DB")
    return result


def wait_for_go2rtc(client: httpx.Client, max_retries: int = 30) -> bool:
    """Chờ go2rtc sẵn sàng trước khi bắt đầu ingest."""
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


def _wait_for_streams(timeout_sec: int = 90) -> list[str]:
    """
    Chờ cho đến khi DB có ít nhất 1 camera enabled.
    Retry mỗi 2s trong timeout_sec giây — tránh race với DB migration startup.
    """
    deadline = time.monotonic() + timeout_sec
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        streams = fetch_active_streams()
        if streams:
            log.info("Found %d camera(s) in DB: %s", len(streams), list(streams.keys()))
            return list(streams.keys())
        log.info("Waiting for cameras in DB... (attempt %d)", attempt)
        time.sleep(2)
    log.warning("Timeout waiting for cameras in DB — will sync on next poll cycle")
    return []


# ── PG NOTIFY loop ─────────────────────────────────────────────────────────────

def listen_loop(manager: StreamManager) -> None:
    """
    Main loop: LISTEN PostgreSQL NOTIFY 'cameras_changed' + fallback poll.

    Camera thay đổi → NOTIFY → re-fetch DB và sync workers ngay lập tức.
    Fallback: poll mỗi POLL_INTERVAL giây để đảm bảo không bỏ sót.
    """
    conn: psycopg2.extensions.connection | None = None

    while _running:
        if conn is None or conn.closed:
            try:
                conn = psycopg2.connect(DB_DSN)
                conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
                cur = conn.cursor()
                cur.execute(f"LISTEN {CHANNEL}")
                cur.close()
                log.info("PG LISTEN registered on '%s'", CHANNEL)
                # Full sync khi mới kết nối
                manager.sync(fetch_active_streams())
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
            if select.select([conn], [], [], POLL_INTERVAL) == ([], [], []):
                # Timeout: fallback sync định kỳ
                manager.sync(fetch_active_streams())
            else:
                conn.poll()
                while conn.notifies:
                    notify = conn.notifies.pop(0)
                    try:
                        payload = json.loads(notify.payload)
                        log.info("Camera changed: op=%s id=%s source_id=%s",
                                 payload.get("op"), payload.get("id"), payload.get("source_id"))
                    except Exception:
                        pass
                    manager.sync(fetch_active_streams())

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


# ── Entry point ────────────────────────────────────────────────────────────────

def _init_zmq():
    """
    Khởi tạo ZMQ PUB socket để push frames vào Savant AI Core.

    Pattern: AI Core SUB binds (tạo endpoint) → rtsp_ingest PUB connects.
    Sau khi connect phải sleep ~500ms để ZMQ subscription propagate
    (tránh "slow joiner syndrome" — các frame đầu tiên bị drop).
    """
    try:
        import zmq
        ctx = zmq.Context()
        sock = ctx.socket(zmq.PUB)
        if "connect" in ZMQ_PUB_ENDPOINT:
            addr = ZMQ_PUB_ENDPOINT.replace("pub+connect:", "")
            sock.connect(addr)
            # Slow-joiner fix: chờ subscription propagate trước khi gửi frame đầu
            time.sleep(0.5)
        else:
            addr = ZMQ_PUB_ENDPOINT.replace("pub+bind:", "")
            sock.bind(addr)
        log.info("ZMQ PUB socket ready: %s", ZMQ_PUB_ENDPOINT)
        return sock
    except ImportError:
        log.warning("pyzmq not installed — ZMQ push disabled. Install: pip install pyzmq")
        return None
    except Exception as exc:
        log.error("ZMQ init failed: %s — frames will not be pushed.", exc)
        return None


def main():
    log.info("═══ SV-PRO RTSP Ingest started ═══")
    log.info("go2rtc: %s | RTSP base: %s", GO2RTC_URL, GO2RTC_RTSP_BASE)
    log.info("ZMQ endpoint: %s | FPS: %d | Poll: %ds", ZMQ_PUB_ENDPOINT, TARGET_FPS, POLL_INTERVAL)

    http_client = httpx.Client(timeout=10.0)

    if not wait_for_go2rtc(http_client):
        log.error("go2rtc not available after retries — exiting.")
        sys.exit(1)

    # Chờ cameras xuất hiện trong DB (tránh race với DB migration startup)
    _wait_for_streams()

    zmq_pub = _init_zmq()
    manager = StreamManager(zmq_publisher=zmq_pub)

    try:
        listen_loop(manager)
    finally:
        manager.stop_all()
        http_client.close()
        log.info("═══ RTSP Ingest stopped ═══")


if __name__ == "__main__":
    main()
