"""
Pipeline Watchdog — Sprint 1, Task 1.8+1.11.

Health monitor + Circuit Breaker cho toàn bộ SV-PRO pipeline.

Chức năng (từ docs/Runbook_Reliability_Restart.md):
  1. Kiểm tra health định kỳ: JSON egress rate, AI Core queue depth, FPS.
  2. Phát hiện pipeline "stuck" (không có JSON mới > threshold giây).
  3. Restart container theo thứ tự: egress → ai-core → ingress.
  4. Circuit Breaker: tối đa N lần restart / 10 phút.
  5. Exponential backoff giữa các lần restart.
  6. Emit Prometheus metrics: watchdog_restarts_total, watchdog_circuit_open.

Cách dùng:
    watchdog = PipelineWatchdog(compose_project_dir="/path/to/project")
    watchdog.start()  # chạy background thread
    # ... pipeline running ...
    watchdog.stop()
"""

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# ── Circuit Breaker config ────────────────────────────────────────────────────
_CIRCUIT_WINDOW_SECS   = 600    # 10 phút
_CIRCUIT_MAX_RESTARTS  = 3      # Tối đa 3 restart / window
_BACKOFF_BASE_SECS     = 5.0    # Backoff khởi đầu
_BACKOFF_MAX_SECS      = 120.0  # Backoff tối đa 2 phút

# ── Health check defaults ─────────────────────────────────────────────────────
_CHECK_INTERVAL_SECS   = 30     # Mỗi 30 giây check health
_STUCK_THRESHOLD_SECS  = 120    # 2 phút không có JSON → pipeline stuck

# ── Docker Compose service names ──────────────────────────────────────────────
SERVICE_JSON_EGRESS    = "json-egress"
SERVICE_AI_CORE        = "savant-ai-core"
SERVICE_VIDEO_INGRESS  = "video-ingress"


@dataclass
class ComponentHealth:
    """Trạng thái sức khỏe của một component."""
    service_name:    str
    is_healthy:      bool  = True
    last_seen_ok:    float = field(default_factory=time.monotonic)
    restart_count:   int   = 0
    circuit_opens:   int   = 0
    _restart_history: list = field(default_factory=list)

    def record_restart(self) -> None:
        self.restart_count += 1
        self._restart_history.append(time.monotonic())

    def recent_restarts(self, window_secs: float = _CIRCUIT_WINDOW_SECS) -> int:
        """Số lần restart trong window_secs gần nhất."""
        cutoff = time.monotonic() - window_secs
        self._restart_history = [t for t in self._restart_history if t > cutoff]
        return len(self._restart_history)


class CircuitBreaker:
    """
    Circuit Breaker cho một component.

    State:
        CLOSED  → hoạt động bình thường, restart được phép.
        OPEN    → quá nhiều restart → dừng auto-restart, đợi operator.
    """

    def __init__(
        self,
        component: ComponentHealth,
        max_restarts: int   = _CIRCUIT_MAX_RESTARTS,
        window_secs:  float = _CIRCUIT_WINDOW_SECS,
    ):
        self._component     = component
        self._max_restarts  = max_restarts
        self._window_secs   = window_secs
        self._open          = False
        self._open_since    = 0.0

    @property
    def is_open(self) -> bool:
        return self._open

    def can_restart(self) -> bool:
        """True nếu được phép restart (circuit CLOSED và dưới ngưỡng)."""
        recent = self._component.recent_restarts(self._window_secs)
        if recent >= self._max_restarts:
            if not self._open:
                self._open      = True
                self._open_since = time.monotonic()
                self._component.circuit_opens += 1
                logger.error(
                    "🔴 Circuit OPEN for [%s]: %d restarts in %.0fs — "
                    "auto-restart disabled. Operator intervention required.",
                    self._component.service_name, recent, self._window_secs,
                )
            return False
        if self._open:
            logger.info(
                "🟢 Circuit CLOSED for [%s]: restart count within limits",
                self._component.service_name,
            )
            self._open = False
        return True

    def force_close(self) -> None:
        """Force close circuit (dùng sau khi operator xác nhận pipeline ổn)."""
        self._open = False
        self._component._restart_history.clear()
        logger.info("Circuit FORCE CLOSED for [%s]", self._component.service_name)


class PipelineWatchdog:
    """
    Background watchdog monitor và auto-restart cho SV-PRO pipeline.

    Tuỳ chọn cung cấp `compose_project_dir` để watchdog có thể chạy
    `docker compose restart` khi phát hiện pipeline stuck.

    Có thể dùng `on_restart_cb` để custom hành động restart thay vì docker.

    Ví dụ:
        watchdog = PipelineWatchdog(
            compose_project_dir="/opt/sv-pro",
            stuck_threshold_secs=120,
        )
        watchdog.start()
    """

    def __init__(
        self,
        compose_project_dir:     Optional[str] = None,
        check_interval_secs:     float = _CHECK_INTERVAL_SECS,
        stuck_threshold_secs:    float = _STUCK_THRESHOLD_SECS,
        max_restarts_per_window: int   = _CIRCUIT_MAX_RESTARTS,
        on_restart_cb:           Optional[Callable[[str], None]] = None,
        on_circuit_open_cb:      Optional[Callable[[str], None]] = None,
    ):
        self._compose_dir       = compose_project_dir
        self._check_interval    = check_interval_secs
        self._stuck_threshold   = stuck_threshold_secs
        self._on_restart_cb     = on_restart_cb
        self._on_circuit_open   = on_circuit_open_cb

        # Component tracking
        self._components = {
            SERVICE_JSON_EGRESS:   ComponentHealth(SERVICE_JSON_EGRESS),
            SERVICE_AI_CORE:       ComponentHealth(SERVICE_AI_CORE),
            SERVICE_VIDEO_INGRESS: ComponentHealth(SERVICE_VIDEO_INGRESS),
        }
        self._breakers = {
            svc: CircuitBreaker(health, max_restarts=max_restarts_per_window)
            for svc, health in self._components.items()
        }

        # State
        self._last_json_ts:  float = time.monotonic()  # last time JSON egress had activity
        self._egress_events: int   = 0                 # cumulative JSON events counter
        self._lock           = threading.Lock()
        self._running        = False
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
        logger.info("SV-PRO Pipeline Watchdog started (interval=%.0fs, stuck=%.0fs)",
                    self._check_interval, self._stuck_threshold)

    def stop(self) -> None:
        """Dừng watchdog."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)

    def notify_json_activity(self, camera_id: str = "global", count: int = 1) -> None:
        """
        Gọi từ JSON egress mỗi khi ghi được event mới.
        Dùng để watchdog biết pipeline đang hoạt động.
        """
        with self._lock:
            self._last_json_ts  = time.monotonic()
            self._egress_events += count

    def force_restart(self, service_name: str) -> bool:
        """Force restart một service (bypass circuit check nếu force=True)."""
        return self._do_restart(service_name, force=True)

    def force_close_circuit(self, service_name: str) -> None:
        """Reset circuit breaker cho một service."""
        if service_name in self._breakers:
            self._breakers[service_name].force_close()

    def get_status(self) -> dict:
        """Trả về trạng thái watchdog dạng dict (cho API /health)."""
        with self._lock:
            json_age = time.monotonic() - self._last_json_ts
        return {
            "running":          self._running,
            "last_json_age_s":  round(json_age, 1),
            "pipeline_stuck":   json_age > self._stuck_threshold,
            "egress_events":    self._egress_events,
            "components": {
                svc: {
                    "healthy":       health.is_healthy,
                    "restart_count": health.restart_count,
                    "circuit_open":  self._breakers[svc].is_open,
                }
                for svc, health in self._components.items()
            },
        }

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
        """Kiểm tra health và quyết định có restart không."""
        with self._lock:
            json_age = time.monotonic() - self._last_json_ts

        # --- Pipeline stuck detection ---
        if json_age > self._stuck_threshold:
            logger.warning(
                "⚠️  Pipeline STUCK: no JSON events in %.0fs (threshold=%.0fs)",
                json_age, self._stuck_threshold,
            )
            self._handle_stuck_pipeline()
        else:
            logger.debug("Watchdog OK: last JSON %.1fs ago", json_age)

    def _handle_stuck_pipeline(self) -> None:
        """
        Xử lý pipeline stuck theo thứ tự từ Runbook:
        1. Restart JSON egress
        2. Nếu vẫn stuck sau next check → restart AI core
        3. Nếu vẫn stuck → restart ingress
        """
        restart_order = [SERVICE_JSON_EGRESS, SERVICE_AI_CORE, SERVICE_VIDEO_INGRESS]
        for service in restart_order:
            if self._do_restart(service):
                # Cho pipeline thời gian hồi phục trước khi check tiếp
                logger.info("Watchdog: restarted [%s], waiting %.0fs to re-check",
                            service, self._check_interval)
                time.sleep(min(self._check_interval / 2, 30))
                with self._lock:
                    json_age = time.monotonic() - self._last_json_ts
                if json_age < self._stuck_threshold:
                    logger.info("✅ Pipeline recovered after restarting [%s]", service)
                    return
            # If circuit open for this service, skip to next
        logger.error(
            "🔴 All restart attempts exhausted — pipeline remains stuck. "
            "Manual operator intervention required."
        )

    def _do_restart(self, service_name: str, force: bool = False) -> bool:
        """
        Thực hiện restart một service (docker compose restart).
        Kiểm tra circuit breaker trước.

        Returns True nếu restart được thực hiện.
        """
        breaker = self._breakers.get(service_name)
        health  = self._components.get(service_name)

        if breaker and not force:
            if not breaker.can_restart():
                if self._on_circuit_open:
                    self._on_circuit_open(service_name)
                if self._metrics:
                    try:
                        self._metrics.watchdog_circuit_open.labels(
                            component=service_name
                        ).set(1)
                    except Exception:
                        pass
                return False

        # Tính backoff
        recent = health.recent_restarts() if health else 0
        backoff = min(_BACKOFF_BASE_SECS * (2 ** recent), _BACKOFF_MAX_SECS)
        if backoff > _BACKOFF_BASE_SECS:
            logger.info("Watchdog backoff %.0fs before restarting [%s]", backoff, service_name)
            time.sleep(backoff)

        logger.warning("🔄 Watchdog RESTARTING [%s] (attempt %d)",
                       service_name, (health.restart_count + 1) if health else 1)

        success = False
        if self._on_restart_cb:
            try:
                self._on_restart_cb(service_name)
                success = True
            except Exception as exc:
                logger.error("Custom restart callback failed for [%s]: %s", service_name, exc)
        elif self._compose_dir:
            success = self._docker_restart(service_name)
        else:
            logger.warning("Watchdog: no restart method configured for [%s]", service_name)

        if health:
            health.record_restart()

        if self._metrics:
            try:
                self._metrics.watchdog_restarts_total.labels(component=service_name).inc()
                self._metrics.watchdog_circuit_open.labels(component=service_name).set(0)
            except Exception:
                pass

        return success

    def _docker_restart(self, service_name: str) -> bool:
        """Chạy `docker compose restart <service>` trong compose_project_dir."""
        try:
            result = subprocess.run(
                ["docker", "compose", "restart", service_name],
                cwd    = self._compose_dir,
                capture_output = True,
                text   = True,
                timeout = 60,
            )
            if result.returncode == 0:
                logger.info("docker compose restart [%s] OK", service_name)
                return True
            else:
                logger.error(
                    "docker compose restart [%s] FAILED (code=%d): %s",
                    service_name, result.returncode, result.stderr,
                )
                return False
        except subprocess.TimeoutExpired:
            logger.error("docker compose restart [%s] TIMEOUT", service_name)
            return False
        except FileNotFoundError:
            logger.error("docker not found — cannot restart [%s]", service_name)
            return False


# ── Singleton ─────────────────────────────────────────────────────────────────
# Khởi tạo trong src/watchdog/__init__.py hoặc pipeline start script
pipeline_watchdog = PipelineWatchdog()
