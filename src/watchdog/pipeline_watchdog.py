"""
Pipeline Watchdog — SV-PRO.

Health monitor cho toàn bộ SV-PRO pipeline.

Chức năng:
  1. Phát hiện pipeline "stuck" (không có JSON mới > threshold giây).
  2. CircuitBreaker per service — giới hạn số lần restart trong cửa sổ thời gian.
  3. Emit Prometheus metrics: watchdog_restarts_total, watchdog_circuit_open.
  4. Gọi callback on_restart_cb nếu được cấu hình.

LÝ DO KHÔNG TỰ RESTART DOCKER:
  - Docker Compose `restart: unless-stopped` + `healthcheck` tự restart container.
  - Tự gọi `docker compose restart` yêu cầu mount /var/run/docker.sock — security risk.
  - Watchdog chỉ cần DETECT + EMIT METRICS + gọi callback ngoài.

Cách dùng:
    watchdog = PipelineWatchdog(on_restart_cb=my_callback)
    watchdog.start()
    watchdog.notify_json_activity("cam_01")  # Gọi khi AI core output có event
    watchdog.stop()
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict

logger = logging.getLogger(__name__)

# ── Service name constants ──────────────────────────────────────────────────────
SERVICE_JSON_EGRESS   = "json_egress"
SERVICE_AI_CORE       = "ai_core"
SERVICE_VIDEO_INGRESS = "video_ingress"

# ── Health check defaults ─────────────────────────────────────────────────────
_CHECK_INTERVAL_SECS   = 30     # Mỗi 30 giây check health
_STUCK_THRESHOLD_SECS  = 120    # 2 phút không có JSON → pipeline stuck
_MAX_RESTARTS_WINDOW   = 5      # Số restart tối đa trong cửa sổ 10 phút
_RESTART_WINDOW_SECS   = 600    # Cửa sổ thời gian tính restart (10 phút)


@dataclass
class ComponentHealth:
    """Trạng thái sức khỏe của một component có lịch sử restart."""
    service_name: str
    is_healthy:   bool = True
    circuit_opens: int = 0

    # Lưu timestamp của mỗi lần restart (dùng để tính recent_restarts)
    _restart_history: list = field(default_factory=list, repr=False)

    @property
    def restart_count(self) -> int:
        """Tổng số lần restart từ trước đến nay."""
        return len(self._restart_history)

    def record_restart(self) -> None:
        """Ghi nhận 1 lần restart."""
        self._restart_history.append(time.monotonic())

    def recent_restarts(self, window_secs: float = _RESTART_WINDOW_SECS) -> int:
        """Đếm số restart trong cửa sổ thời gian gần đây."""
        cutoff = time.monotonic() - window_secs
        return sum(1 for ts in self._restart_history if ts >= cutoff)


class CircuitBreaker:
    """
    Circuit breaker per service — mở khi quá nhiều restart trong cửa sổ thời gian.

    Khi mở (is_open=True): can_restart() trả False, ngăn restart vô hạn.
    Force close: force_close() để reset và cho phép restart lại.
    """

    def __init__(
        self,
        health: ComponentHealth,
        max_restarts: int = _MAX_RESTARTS_WINDOW,
        window_secs: float = _RESTART_WINDOW_SECS,
    ):
        self._health      = health
        self._max_restarts = max_restarts
        self._window_secs  = window_secs
        self._open         = False

    @property
    def is_open(self) -> bool:
        return self._open

    def can_restart(self) -> bool:
        """Trả True nếu circuit closed và chưa đạt giới hạn restart."""
        if self._open:
            return False
        if self._health.recent_restarts(self._window_secs) >= self._max_restarts:
            self._open = True
            self._health.circuit_opens += 1
            logger.warning(
                "Circuit OPEN for [%s]: %d restarts in %.0fs window.",
                self._health.service_name, self._max_restarts, self._window_secs,
            )
            return False
        return True

    def force_close(self) -> None:
        """Đóng circuit breaker và xóa lịch sử restart (cho phép restart lại)."""
        self._open = False
        self._health._restart_history.clear()
        logger.info("Circuit CLOSED for [%s] (forced, history cleared).", self._health.service_name)


class PipelineWatchdog:
    """
    Background watchdog monitor cho SV-PRO pipeline.

    Phát hiện pipeline stuck và emit Prometheus metrics.
    Restart được xử lý bởi Docker Compose restart policy hoặc on_restart_cb callback.
    """

    def __init__(
        self,
        check_interval_secs:   float = _CHECK_INTERVAL_SECS,
        stuck_threshold_secs:  float = _STUCK_THRESHOLD_SECS,
        max_restarts_per_window: int = _MAX_RESTARTS_WINDOW,
        restart_window_secs:   float = _RESTART_WINDOW_SECS,
        on_restart_cb:  Optional[Callable[[str], bool]] = None,
        on_stuck_cb:    Optional[Callable[[str], None]] = None,
    ):
        self._check_interval    = check_interval_secs
        self._stuck_threshold   = stuck_threshold_secs
        self._max_restarts      = max_restarts_per_window
        self._restart_window    = restart_window_secs
        self._on_restart_cb     = on_restart_cb
        # on_stuck_cb kept for backward compat (wrapped into restart callback)
        self._on_stuck_cb       = on_stuck_cb

        # Per-service health + circuit breaker
        _services = [SERVICE_JSON_EGRESS, SERVICE_AI_CORE, SERVICE_VIDEO_INGRESS]
        self._health: Dict[str, ComponentHealth] = {
            svc: ComponentHealth(svc) for svc in _services
        }
        self._circuits: Dict[str, CircuitBreaker] = {
            svc: CircuitBreaker(self._health[svc], max_restarts=max_restarts_per_window,
                                window_secs=restart_window_secs)
            for svc in _services
        }

        # State theo dõi JSON egress activity
        self._last_json_ts:   float = time.monotonic()
        self._egress_events:  int   = 0
        self._stuck_notified: bool  = False
        self._lock            = threading.Lock()
        self._running         = False
        self._thread: Optional[threading.Thread] = None

        # Lazy telemetry
        try:
            from src.telemetry import metrics
            self._metrics = metrics
        except Exception:
            self._metrics = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Khởi động watchdog background thread."""
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._watch_loop,
            daemon=True,
            name="svpro-watchdog",
        )
        self._thread.start()
        logger.info(
            "SV-PRO Pipeline Watchdog started (interval=%.0fs, stuck_threshold=%.0fs)",
            self._check_interval, self._stuck_threshold,
        )

    def stop(self) -> None:
        """Dừng watchdog."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)

    def notify_json_activity(self, camera_id: str = "global", count: int = 1) -> None:
        """
        Gọi từ audit_logger mỗi khi ghi được event mới.
        Dùng để watchdog biết pipeline đang hoạt động.
        """
        with self._lock:
            self._last_json_ts    = time.monotonic()
            self._egress_events  += count
            self._stuck_notified  = False

    def get_status(self) -> dict:
        """Trả về trạng thái watchdog dạng dict (cho API /health)."""
        with self._lock:
            json_age = time.monotonic() - self._last_json_ts
        return {
            "running":         self._running,
            "last_json_age_s": round(json_age, 1),
            "pipeline_stuck":  json_age > self._stuck_threshold,
            "egress_events":   self._egress_events,
            "components":      {
                svc: {
                    "is_healthy":    h.is_healthy,
                    "restart_count": h.restart_count,
                    "circuit_open":  self._circuits[svc].is_open,
                }
                for svc, h in self._health.items()
            },
        }

    def force_restart(self, service_name: str) -> bool:
        """
        Buộc restart 1 service — bypass circuit breaker.
        Gọi on_restart_cb(service_name) nếu có.
        Trả về True nếu callback thành công.
        """
        return self._do_restart(service_name, force=True)

    def _do_restart(self, service_name: str, force: bool = False) -> bool:
        """
        Thực hiện restart 1 service qua callback.

        force=True: bỏ qua circuit breaker.
        force=False: kiểm tra circuit breaker trước.
        """
        circuit = self._circuits.get(service_name)
        if circuit is None:
            # Service không xác định → tạo mới on-the-fly
            health = ComponentHealth(service_name)
            circuit = CircuitBreaker(health, max_restarts=self._max_restarts,
                                     window_secs=self._restart_window)
            self._health[service_name]   = health
            self._circuits[service_name] = circuit

        if not force and not circuit.can_restart():
            logger.warning("Restart blocked by circuit breaker for [%s].", service_name)
            return False

        self._health[service_name].record_restart()

        if self._on_restart_cb:
            try:
                result = self._on_restart_cb(service_name)
                if self._metrics:
                    try:
                        self._metrics.watchdog_restarts_total.labels(
                            component=service_name
                        ).inc()
                    except Exception:
                        pass
                return bool(result)
            except Exception as exc:
                logger.error("on_restart_cb error for [%s]: %s", service_name, exc)
                return False

        return True

    def force_close_circuit(self, service_name: str) -> None:
        """Đóng circuit breaker cho 1 service cụ thể."""
        circuit = self._circuits.get(service_name)
        if circuit:
            circuit.force_close()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _watch_loop(self) -> None:
        """Background check loop."""
        while self._running:
            try:
                self._check_pipeline_health()
            except Exception as exc:
                logger.error("Watchdog check error: %s", exc, exc_info=True)
            time.sleep(self._check_interval)

    def _check_pipeline_health(self) -> None:
        """Kiểm tra health và emit metrics / callback khi stuck."""
        with self._lock:
            json_age = time.monotonic() - self._last_json_ts

        is_stuck = json_age > self._stuck_threshold

        if self._metrics:
            try:
                self._metrics.watchdog_circuit_open.labels(
                    component="pipeline"
                ).set(1 if is_stuck else 0)
            except Exception:
                pass

        if is_stuck:
            if not self._stuck_notified:
                logger.warning(
                    "⚠️  Pipeline STUCK: no JSON events in %.0fs (threshold=%.0fs). "
                    "Docker Compose will restart unhealthy containers automatically.",
                    json_age, self._stuck_threshold,
                )
                self._stuck_notified = True

                if self._on_stuck_cb:
                    try:
                        self._on_stuck_cb("pipeline_stuck")
                    except Exception as exc:
                        logger.debug("on_stuck_cb error: %s", exc)
        else:
            logger.debug("Watchdog OK: last JSON %.1fs ago", json_age)


# ── Singleton ─────────────────────────────────────────────────────────────────
pipeline_watchdog = PipelineWatchdog()
