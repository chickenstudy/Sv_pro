"""
Dynamic RTSP Ingress Manager — SV-PRO.

Đọc danh sách cameras từ PostgreSQL, tự động spawn/kill GStreamer
RTSP→ZMQ subprocess cho mỗi camera enabled.

Polling interval: 10s (cấu hình qua POLL_INTERVAL_SECS).
Khi camera bị disable hoặc xóa → kill subprocess tương ứng.
Khi camera mới được thêm hoặc rtsp_url thay đổi → restart subprocess.

EOS Guard Integration:
  - Theo dõi EOS storm events từ subprocess stderr
  - Tự động restart khi detect EOS storm để reset stream state
  - Gửi metrics về disconnect/reconnect events cho monitoring
"""

import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

import psycopg2

# EOS Guard integration
from src.ingress.eos_guard import EosGuardRegistry

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("ingress-manager")

# ── Config from env ─────────────────────────────────────────────────────────────
DB_DSN = os.environ.get(
    "POSTGRES_DSN",
    "postgresql://svpro_user:svpro_pass@postgres:5432/svpro_db",
)
ZMQ_ENDPOINT = os.environ.get(
    "ZMQ_ENDPOINT",
    "pub+connect:ipc:///tmp/zmq-sockets/input-video.ipc",
)
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECS", "10"))
GST_PLUGIN_PATH = os.environ.get(
    "GST_PLUGIN_PATH",
    "/opt/savant/adapters/gst/gst_plugins",
)
RTSP_SCRIPT = os.environ.get(
    "RTSP_SCRIPT",
    "/opt/savant/adapters/gst/sources/rtsp.sh",
)

_running = True


def _sighandler(sig, _frame):
    global _running
    log.info("Received signal %s — shutting down.", sig)
    _running = False


signal.signal(signal.SIGINT, _sighandler)
# SIGTERM không tồn tại trên Windows — chỉ đăng ký trên Unix
if sys.platform != "win32":
    signal.signal(signal.SIGTERM, _sighandler)


@dataclass(frozen=True)
class CameraRow:
    """Immutable snapshot of a camera row from DB."""
    id: int
    name: str
    rtsp_url: str
    source_id: str
    zone: str
    fps_limit: int
    enabled: bool


def fetch_cameras(dsn: str) -> list[CameraRow]:
    """Query enabled cameras from PostgreSQL."""
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, rtsp_url, "
            "COALESCE(name, 'cam_' || id) AS source_id, "
            "COALESCE(zone, 'default') AS zone, "
            "fps_limit, enabled "
            "FROM cameras WHERE enabled = true "
            "ORDER BY id"
        )
        rows = [CameraRow(*r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as exc:
        log.error("DB query failed: %s", exc)
        return []


def _build_env(cam: CameraRow) -> dict:
    """Build environment dict for the RTSP adapter subprocess."""
    env = os.environ.copy()
    env.update({
        "SOURCE_ID": cam.source_id,
        "RTSP_URI": cam.rtsp_url,
        "ZMQ_ENDPOINT": ZMQ_ENDPOINT,
        "SYNC_OUTPUT": "false",
        "RTSP_TRANSPORT": "tcp",
        "FPS_PERIOD_SECONDS": "10",
        "ZMQ_SNDHWM": os.environ.get("ZMQ_SNDHWM", "100"),
        "GST_PLUGIN_PATH": GST_PLUGIN_PATH,
        "PROJECT_PATH": "/opt/savant",
    })
    return env


class IngressManager:
    """Manages one subprocess per active camera with EOS Guard integration."""

    def __init__(self):
        # key = camera.id, value = (CameraRow, subprocess.Popen)
        self._procs: dict[int, tuple[CameraRow, subprocess.Popen]] = {}
        self._eos_registry = EosGuardRegistry(
            threshold=int(os.environ.get("EOS_THRESHOLD", "5")),
            window_secs=float(os.environ.get("EOS_WINDOW_SECS", "1.0")),
            on_storm=self._on_eos_storm,
            on_reconnect=self._on_reconnect,
        )
        # Lazy import telemetry
        self._metrics = None
        try:
            from src.telemetry import metrics
            self._metrics = metrics
        except Exception:
            pass

    def _on_eos_storm(self, source_id: str) -> None:
        """Callback khi EOS storm được phát hiện."""
        log.warning(
            "[%s] EOS Storm detected — scheduling process restart",
            source_id,
        )
        # Tìm camera theo source_id và restart
        for cam_id, (cam, proc) in list(self._procs.items()):
            if cam.source_id == source_id:
                log.info("[%s] Restarting due to EOS storm", source_id)
                self._stop(cam_id)
                self._start(cam)
                # Record disconnect metric
                if self._metrics:
                    try:
                        self._metrics.rtsp_disconnect_total.labels(
                            camera_id=source_id,
                            reason="eos_storm",
                        ).inc()
                    except Exception:
                        pass
                break

    def _on_reconnect(self, source_id: str) -> None:
        """Callback khi reconnect thành công sau EOS storm."""
        log.info("[%s] Reconnected successfully after EOS storm", source_id)
        if self._metrics:
            try:
                self._metrics.rtsp_reconnect_total.labels(
                    camera_id=source_id,
                ).inc()
            except Exception:
                pass

    def sync(self, cameras: list[CameraRow]):
        """Sync running processes with the desired camera list."""
        desired_ids = {c.id for c in cameras}
        cam_map = {c.id: c for c in cameras}

        # Stop processes for removed/disabled cameras
        to_remove = [cid for cid in self._procs if cid not in desired_ids]
        for cid in to_remove:
            self._stop(cid)

        # Start or restart cameras
        for cam in cameras:
            existing = self._procs.get(cam.id)
            if existing is None:
                self._start(cam)
            else:
                old_cam, proc = existing
                # Restart if config changed or process died
                if old_cam.rtsp_url != cam.rtsp_url or proc.poll() is not None:
                    reason = "config changed" if old_cam.rtsp_url != cam.rtsp_url else "process died"
                    log.info("Restarting [%s] (%s): %s", cam.source_id, cam.id, reason)
                    self._stop(cam.id)
                    self._start(cam)

    def _start(self, cam: CameraRow):
        log.info(
            "Starting RTSP ingress [%s] (id=%d) → %s",
            cam.source_id, cam.id, cam.rtsp_url[:60] + "...",
        )
        try:
            proc = subprocess.Popen(
                [RTSP_SCRIPT],
                env=_build_env(cam),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._procs[cam.id] = (cam, proc)
            # Start stderr monitoring thread for EOS events
            thread = threading.Thread(
                target=self._monitor_stderr,
                args=(cam.id, cam.source_id, proc),
                daemon=True,
            )
            thread.start()
            log.debug(
                "[%s] stderr monitor thread started for PID %d",
                cam.source_id, proc.pid,
            )
        except Exception as exc:
            log.error("Failed to start [%s]: %s", cam.source_id, exc)

    def _monitor_stderr(self, cam_id: int, source_id: str, proc: subprocess.Popen):
        """
        Monitor stderr for EOS-related events from the RTSP subprocess.

        Parses GStreamer output to detect EOS events and feeds them to the
        EOS Guard for rate limiting.
        """
        import fcntl
        import select

        # fcntl không tồn tại trên Windows — skip non-blocking setup
        if sys.platform != "win32":
            try:
                fd = proc.stderr.fileno()
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            except Exception:
                pass

        eos_pattern = re.compile(r"(EOS|end-of-stream|not a keyframe)", re.IGNORECASE)
        reconnect_pattern = re.compile(r"(reconnect|rtsp.*connect|connection.*lost)", re.IGNORECASE)

        while proc.poll() is None:
            try:
                if sys.platform == "win32":
                    # Windows: simple polling
                    time.sleep(0.1)
                    try:
                        line = proc.stderr.readline()
                    except Exception:
                        continue
                else:
                    # Unix: use select
                    fd = proc.stderr.fileno()
                    ready, _, _ = select.select([fd], [], [], 0.1)
                    if not ready:
                        continue
                    # Read available bytes without blocking past end of line
                    data = b""
                    while True:
                        chunk = os.read(fd, 1024)
                        if not chunk:
                            break
                        data += chunk
                    line = data.decode("utf-8", errors="replace")

                if not line:
                    continue

                # Check for EOS patterns
                if eos_pattern.search(line):
                    log.debug("[%s] EOS event detected in stderr", source_id)
                    # Feed to EOS guard (record received)
                    guard = self._eos_registry.get(source_id)
                    guard.record_eos_received()
                    if not guard.should_forward():
                        log.debug(
                            "[%s] EOS suppressed by guard (storm active)",
                            source_id,
                        )
                    # else: EOS forwarded normally

                # Check for reconnect patterns
                if reconnect_pattern.search(line):
                    log.info(
                        "[%s] Reconnect event detected in stderr",
                        source_id,
                    )

            except (OSError, IOError):
                # Stderr closed or empty
                break
            except Exception as exc:
                log.debug("[%s] stderr monitor error: %s", source_id, exc)
                break

        log.debug("[%s] stderr monitor thread exiting", source_id)

    def _stop(self, cam_id: int):
        entry = self._procs.pop(cam_id, None)
        if entry is None:
            return
        cam, proc = entry
        log.info("Stopping RTSP ingress [%s] (id=%d)", cam.source_id, cam_id)
        # Reset EOS guard for this camera
        guard = self._eos_registry.get(cam.source_id)
        guard.reset()
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        except Exception:
            pass

    def stop_all(self):
        for cid in list(self._procs):
            self._stop(cid)

    def status(self) -> dict:
        return {
            cid: {
                "source_id": cam.source_id,
                "rtsp_url": cam.rtsp_url[:40],
                "alive": proc.poll() is None,
            }
            for cid, (cam, proc) in self._procs.items()
        }


def main():
    log.info("═══ SV-PRO Ingress Manager started ═══")
    log.info("DB: %s", DB_DSN.split("@")[-1])  # Log host only
    log.info("ZMQ: %s", ZMQ_ENDPOINT)
    log.info("Poll: %ds", POLL_INTERVAL)
    log.info("EOS Threshold: %d EOS/s, Window: %.1fs",
             int(os.environ.get("EOS_THRESHOLD", "5")),
             float(os.environ.get("EOS_WINDOW_SECS", "1.0")))

    mgr = IngressManager()

    # Wait for DB to be ready
    for attempt in range(30):
        cameras = fetch_cameras(DB_DSN)
        if cameras is not None:
            break
        log.info("Waiting for DB... (attempt %d/30)", attempt + 1)
        time.sleep(2)

    while _running:
        cameras = fetch_cameras(DB_DSN)
        mgr.sync(cameras)

        status = mgr.status()
        active = sum(1 for s in status.values() if s["alive"])
        active_storms = mgr._eos_registry.active_storms()

        if active_storms:
            log.warning(
                "Cameras: %d active / %d total | Active EOS storms: %s",
                active, len(status), active_storms,
            )
        else:
            log.info(
                "Cameras: %d active / %d total",
                active, len(status),
            )

        # Sleep in small increments so we can react to signals
        for _ in range(POLL_INTERVAL * 2):
            if not _running:
                break
            time.sleep(0.5)

    log.info("Shutting down all ingress processes...")
    mgr.stop_all()
    log.info("═══ Ingress Manager stopped ═══")


if __name__ == "__main__":
    main()
