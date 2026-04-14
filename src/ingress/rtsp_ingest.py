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
    _SAVANT_RS_OK = True
except ImportError as _savant_err:
    _SAVANT_RS_OK = False
    # Log lỗi sau khi logging được khởi tạo (xem phần cuối module)
    _SAVANT_RS_ERR = _savant_err

logging.basicConfig(
    level=logging.INFO,
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

        go2rtc xử lý reconnect khi camera gốc bị ngắt — RTSP re-stream
        của go2rtc sẽ drop rồi phục hồi mà không cần EOS guard thủ công.
        """
        frame_interval = 1.0 / max(TARGET_FPS, 1)
        last_frame_time = 0.0
        cap: Optional[cv2.VideoCapture] = None

        while not self._stop_event.is_set():
            # Kết nối RTSP nếu chưa có hoặc bị mất
            if cap is None or not cap.isOpened():
                rtsp_url = f"{GO2RTC_RTSP_BASE}/{self.config.source_id}"
                log.info("Connecting to RTSP: %s", rtsp_url)
                cap = cv2.VideoCapture(rtsp_url)
                if not cap.isOpened():
                    log.warning("[%s] Cannot open RTSP — retry in 5s", self.config.source_id)
                    time.sleep(5)
                    continue
                # Giới hạn internal buffer của OpenCV về 1 frame.
                # Mặc định OpenCV buffer 4-10 frame → Savant nhận frame cũ
                # thay vì frame mới nhất khi pipeline chậm hơn capture rate.
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                log.info("✅ Connected: [%s]", self.config.source_id)

            # Throttle FPS để không gửi quá nhanh vào DeepStream
            now = time.monotonic()
            elapsed = now - last_frame_time
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)

            ret, frame = cap.read()
            if not ret or frame is None:
                log.warning("[%s] Frame read failed — reconnecting.", self.config.source_id)
                cap.release()
                cap = None
                time.sleep(2)
                continue

            last_frame_time = time.monotonic()

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
        ok, jpeg_buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok or jpeg_buf is None:
            log.warning("[%s] cv2.imencode failed — frame skipped", source_id)
            return
        jpeg_bytes = jpeg_buf.tobytes()

        # PTS tăng đơn điệu per source_id (µs)
        pts_step = int(_PTS_TIME_BASE[1] / max(TARGET_FPS, 1))
        pts = _pts_counters.get(source_id, 0)
        _pts_counters[source_id] = pts + pts_step

        # Tạo Savant RS VideoFrame
        vf = VideoFrame(
            source_id=source_id,
            framerate=f"{TARGET_FPS}/1",
            width=w,
            height=h,
            transcoding_method=VideoFrameTranscodingMethod.Copy,
            codec="jpeg",
            keyframe=True,    # mỗi JPEG là independent frame
            pts=pts,
            dts=None,
            duration=None,
            time_base=_PTS_TIME_BASE,
        )
        vf.content = VideoFrameContent.internal(jpeg_bytes)

        # Serialize → protobuf bytes → gửi ZMQ multipart
        # Lock bắt buộc: ZMQ socket không thread-safe, mỗi StreamWorker
        # chạy trên thread riêng và gọi hàm này đồng thời.
        msg = SavantMessage.video_frame(vf)
        data = msg.serialize()
        with _zmq_lock:
            publisher.send_multipart([source_id.encode(), data])

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

def fetch_active_streams() -> dict[str, str]:
    """
    Lấy danh sách cameras enabled từ PostgreSQL và map sang go2rtc RTSP re-stream URL.

    Trả về {source_id: rtsp://svpro-go2rtc:8554/{source_id}}.

    Không dùng GET /api/streams của go2rtc vì go2rtc 1.9.x dùng lazy-connection model:
    dynamic streams thêm qua REST API chỉ hiện trong /api/streams khi đã có consumer
    → GET trả về {} mặc dù PUT đã thành công 200 OK.

    source_id = camera.name nếu có, ngược lại 'cam_{id}'
    — khớp với go2rtc_sync.py và NOTIFY trigger COALESCE(name, 'cam_' || id::text).
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
        result: dict[str, str] = {}
        for row_id, row_name, _rtsp, _enabled in rows:
            sid = row_name if row_name else f"cam_{row_id}"
            result[sid] = f"{GO2RTC_RTSP_BASE}/{sid}"
        if result:
            log.info("DB cameras for ingest: %s", list(result.keys()))
        else:
            log.warning("No enabled cameras found in DB")
        return result
    except Exception as exc:
        log.error("DB fetch_cameras failed: %s", exc)
        return {}


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
