"""
EOS Storm Guard — Sprint 1, Task 1.7.

Phát hiện và chặn EOS storm từ RTSP disconnect liên tục.

Ngữ cảnh (từ docs/Reliability_Backpressure_ZMQ_EOS_Deadlock.md):
  - Camera RTSP bị ngắt đột ngột → GStreamer bắn EOS liên tục (EOS storm).
  - Nếu EOS được forward vào ZMQ → queue tràn → AI Core treo / crash loop.
  - Guard: đếm EOS/giây; nếu > threshold → flush + reconnect, KHÔNG forward EOS.

Thread-safe: có thể gọi từ nhiều GStreamer callback thread đồng thời.
"""

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Config defaults ───────────────────────────────────────────────────────────
_DEFAULT_EOS_THRESHOLD = 5      # EOS/giây tối đa trước khi guard kích hoạt
_DEFAULT_WINDOW_SECS   = 1.0    # Cửa sổ đo EOS rate (giây)
_COOLDOWN_SECS         = 5.0    # Sau khi guard, chờ bao lâu trước khi reset


class EosStormGuard:
    """
    Guard chống EOS storm cho một camera/source.

    Cách dùng trong GStreamer pipeline callback:

        guard = EosStormGuard(
            source_id   = "cam_01",
            threshold   = 5,
            on_storm    = lambda sid: logger.warning("EOS storm %s", sid),
        )

        def on_eos(event):
            if guard.should_forward(event):
                zmq_socket.send(eos_packet)
    """

    def __init__(
        self,
        source_id:       str,
        threshold:       int = _DEFAULT_EOS_THRESHOLD,
        window_secs:     float = _DEFAULT_WINDOW_SECS,
        on_storm:        Optional[Callable[[str], None]] = None,
        on_reconnect:    Optional[Callable[[str], None]] = None,
    ):
        """
        Args:
            source_id:    ID camera/source để log.
            threshold:    EOS/giây tối đa. Vượt ngưỡng → storm guard kích hoạt.
            window_secs:  Cửa sổ thời gian đếm EOS rate.
            on_storm:     Callback khi storm phát hiện (để trigger reconnect RTSP).
            on_reconnect: Callback sau khi guard reset (pipeline ready lại).
        """
        self._source_id     = source_id
        self._threshold     = threshold
        self._window        = window_secs
        self._on_storm      = on_storm
        self._on_reconnect  = on_reconnect

        self._lock          = threading.Lock()
        self._eos_counter   = 0
        self._window_start  = time.monotonic()
        self._storm_active  = False
        self._storm_start   = 0.0

        # Lazy import để không crash nếu telemetry không cài
        try:
            from src.telemetry import metrics, record_drop, DropReason
            self._metrics      = metrics
            self._record_drop  = record_drop
            self._drop_reason  = DropReason
        except Exception:
            self._metrics      = None
            self._record_drop  = lambda *a, **kw: None
            self._drop_reason  = type("DR", (), {
                "EOS_STORM_GUARDED": "eos_storm_guarded",
                "QUEUE_FULL": "queue_full",
                "SEND_TIMEOUT": "send_timeout",
            })()

    def record_eos_received(self) -> None:
        """Ghi nhận một EOS event nhận được từ RTSP."""
        if self._metrics:
            try:
                self._metrics.eos_received_total.labels(
                    camera_id=self._source_id
                ).inc()
            except Exception:
                pass

    def record_eos_forwarded(self) -> None:
        """Ghi nhận một EOS event được forward vào ZMQ."""
        if self._metrics:
            try:
                self._metrics.eos_forwarded_total.labels(
                    camera_id=self._source_id
                ).inc()
            except Exception:
                pass

    def record_eos_dropped(self) -> None:
        """Ghi nhận một EOS event bị guard drop."""
        if self._metrics:
            try:
                self._metrics.eos_dropped_total.labels(
                    camera_id=self._source_id
                ).inc()
            except Exception:
                pass

    # ── Public API ────────────────────────────────────────────────────────────

    def should_forward(self, event=None) -> bool:
        """
        Kiểm tra xem EOS event có nên được forward vào ZMQ không.

        Returns:
            True  → forward EOS bình thường.
            False → storm guard đang hoạt động, bỏ qua EOS này.
        """
        with self._lock:
            now = time.monotonic()

            # Kiểm tra nếu storm đang trong cooldown
            if self._storm_active:
                elapsed = now - self._storm_start
                if elapsed < _COOLDOWN_SECS:
                    logger.debug(
                        "EOS Guard [%s]: storm cooldown %.1fs remain",
                        self._source_id, _COOLDOWN_SECS - elapsed,
                    )
                    self._record_drop(
                        camera_id=self._source_id,
                        component="ingress",
                        reason=self._drop_reason.EOS_STORM_GUARDED,
                    )
                    self.record_eos_dropped()
                    return False
                else:
                    # Cooldown xong → reset
                    self._reset_storm()
                    if self._on_reconnect:
                        threading.Thread(
                            target=self._on_reconnect,
                            args=(self._source_id,),
                            daemon=True,
                        ).start()

            # Reset cửa sổ đếm nếu đã qua
            window_elapsed = now - self._window_start
            if window_elapsed >= self._window:
                self._eos_counter  = 0
                self._window_start = now

            # Tăng counter
            self._eos_counter += 1

            # Kiểm tra vượt ngưỡng
            if self._eos_counter > self._threshold:
                self._activate_storm(now)
                self.record_eos_dropped()
                return False

            # Forward bình thường
            self.record_eos_forwarded()
            return True

    def reset(self) -> None:
        """Force reset guard state (dùng khi camera reconnect thành công)."""
        with self._lock:
            self._reset_storm()
            self._eos_counter  = 0
            self._window_start = time.monotonic()
        logger.info("EOS Guard [%s]: manually reset", self._source_id)

    @property
    def is_storm_active(self) -> bool:
        """True nếu đang trong trạng thái storm guard."""
        return self._storm_active

    @property
    def eos_rate(self) -> float:
        """EOS rate ước tính (EOS/giây) trong cửa sổ hiện tại."""
        with self._lock:
            elapsed = max(time.monotonic() - self._window_start, 0.001)
            return self._eos_counter / elapsed

    # ── Private ───────────────────────────────────────────────────────────────

    def _activate_storm(self, now: float) -> None:
        """Kích hoạt storm guard."""
        self._storm_active = True
        self._storm_start  = now
        rate = self._eos_counter / max(now - self._window_start, 0.001)
        logger.warning(
            "🌪️  EOS Storm DETECTED [%s]: %.1f EOS/s > threshold=%d — "
            "guard activated, dropping EOS for %.0fs",
            self._source_id, rate, self._threshold, _COOLDOWN_SECS,
        )
        # Telemetry
        if self._metrics:
            try:
                self._metrics.eos_storm_detected.labels(
                    camera_id=self._source_id
                ).inc()
            except Exception:
                pass
        # Trigger callback bất đồng bộ để không chặn GStreamer thread
        if self._on_storm:
            threading.Thread(
                target=self._on_storm,
                args=(self._source_id,),
                daemon=True,
            ).start()

    def _reset_storm(self) -> None:
        """Reset storm state (phải gọi khi đang giữ lock)."""
        if self._storm_active:
            logger.info("EOS Guard [%s]: storm cooldown ended — resuming normal EOS forwarding", self._source_id)
        self._storm_active = False
        self._storm_start  = 0.0


# ── Multi-camera Registry ─────────────────────────────────────────────────────

class EosGuardRegistry:
    """
    Registry quản lý EosStormGuard cho nhiều camera.

    Dùng singleton `eos_guard_registry` bên dưới.
    `savant-video-ingress` adapter gọi .get(source_id) cho mỗi EOS event.
    """

    def __init__(
        self,
        threshold:    int   = _DEFAULT_EOS_THRESHOLD,
        window_secs:  float = _DEFAULT_WINDOW_SECS,
        on_storm:     Optional[Callable[[str], None]] = None,
        on_reconnect: Optional[Callable[[str], None]] = None,
    ):
        self._guards: dict[str, EosStormGuard] = {}
        self._lock          = threading.Lock()
        self._threshold     = threshold
        self._window_secs   = window_secs
        self._on_storm      = on_storm
        self._on_reconnect  = on_reconnect

    def get(self, source_id: str) -> EosStormGuard:
        """Lấy (hoặc tạo mới) guard cho source_id."""
        with self._lock:
            if source_id not in self._guards:
                self._guards[source_id] = EosStormGuard(
                    source_id    = source_id,
                    threshold    = self._threshold,
                    window_secs  = self._window_secs,
                    on_storm     = self._on_storm,
                    on_reconnect = self._on_reconnect,
                )
        return self._guards[source_id]

    def should_forward(self, source_id: str, event=None) -> bool:
        """Shortcut: kiểm tra và forward EOS theo source_id."""
        return self.get(source_id).should_forward(event)

    def active_storms(self) -> list[str]:
        """Danh sách source_id đang trong trạng thái storm."""
        with self._lock:
            return [sid for sid, g in self._guards.items() if g.is_storm_active]


# ── Singleton ─────────────────────────────────────────────────────────────────
eos_guard_registry = EosGuardRegistry()
