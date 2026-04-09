"""
SV-PRO Telemetry — Prometheus Metrics Collector.

Định nghĩa và export toàn bộ metrics theo contract:
  docs/Telemetry_Metrics_DropReason_Contract.md

Sử dụng thư viện prometheus_client (Python).
Tất cả metric là Gauge/Counter/Histogram với label chuẩn:
  camera_id, component, model, result

Cách dùng trong pyfunc:
    from src.telemetry import metrics
    metrics.frames_ingressed.labels(camera_id="cam_01").inc()

Cách dùng với HTTP server (standalone):
    from src.telemetry import start_metrics_server
    start_metrics_server(port=9100)
"""

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ── Try to import prometheus_client (optional dependency) ─────────────────────
try:
    from prometheus_client import (
        Counter, Gauge, Histogram, Summary,
        CollectorRegistry, start_http_server, REGISTRY,
        make_wsgi_app,
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_AVAILABLE = False
    logger.warning(
        "prometheus_client not installed — telemetry metrics will be no-ops. "
        "Install with: pip install prometheus-client"
    )

# ── Drop Reason Codes (theo contract) ─────────────────────────────────────────
class DropReason:
    QUEUE_FULL         = "queue_full"
    SEND_TIMEOUT       = "send_timeout"
    SUBSCRIBER_LAG     = "subscriber_lag"
    EOS_STORM_GUARDED  = "eos_storm_guarded"
    SOCKET_STATE_ERROR = "socket_state_error"
    DISK_FULL          = "disk_full"
    WRITER_QUEUE_FULL  = "writer_queue_full"
    CONFIG_INVALID     = "yaml_or_config_invalid"
    LOW_QUALITY        = "low_quality_frame"
    OCR_FAIL           = "ocr_low_confidence"


# ── Metric Namespace ──────────────────────────────────────────────────────────
_NS = "svpro"


# ── No-op shim khi prometheus_client không có ─────────────────────────────────
class _NoOpMetric:
    """Placeholder khi prometheus_client không được cài."""
    def labels(self, **_):        return self
    def inc(self, amount=1):      pass
    def dec(self, amount=1):      pass
    def set(self, value):         pass
    def observe(self, value):     pass
    def time(self):
        import contextlib
        return contextlib.nullcontext()


class _NoOpMetrics:
    def __getattr__(self, _):
        return _NoOpMetric()


# ── Metrics Definition ─────────────────────────────────────────────────────────

class SvProMetrics:
    """
    Singleton container cho tất cả Prometheus metrics của SV-PRO.

    Labels chuẩn theo contract:
        camera_id  — source_id của camera
        component  — ingress | aicore | json_egress | video_egress
        model      — plate_detector | ocr | scrfd | arcface | none
        result     — ok | dropped | error
    """

    def __init__(self, registry=None):
        if not _PROMETHEUS_AVAILABLE:
            self._noop = True
            return
        self._noop = False
        reg = registry  # None = dùng global REGISTRY

        # ── Frames processed (AI core) ─────────────────────────────────────────
        self.frames_processed_total = Counter(
            f"{_NS}_frames_processed_total",
            "Tổng số frames đã xử lý (per camera/source_id) ở AI core",
            ["source_id"],
            registry=reg,
        )

        # ── Events produced (AI core) ─────────────────────────────────────────
        self.lpr_events_total = Counter(
            f"{_NS}_lpr_events_total",
            "Tổng số sự kiện LPR được tạo ra",
            ["camera_id"],
            registry=reg,
        )

        self.fr_events_total = Counter(
            f"{_NS}_fr_events_total",
            "Tổng số sự kiện FR được tạo ra",
            ["camera_id"],
            registry=reg,
        )

        # ── Ingress FPS ────────────────────────────────────────────────────────
        self.ingress_fps = Gauge(
            f"{_NS}_ingress_fps",
            "FPS thực tế decode+đẩy vào ZMQ",
            ["camera_id"],
            registry=reg,
        )

        # ── AI Core queue depth (backpressure) ────────────────────────────────
        self.aicore_queue_depth = Gauge(
            f"{_NS}_aicore_queue_depth",
            "Số messages đang trong buffer AI core",
            ["camera_id"],
            registry=reg,
        )

        # ── Ingress send latency ───────────────────────────────────────────────
        self.ingress_send_latency_ms = Gauge(
            f"{_NS}_ingress_send_latency_ms",
            "Latency gửi frame từ ingress vào ZMQ (ms)",
            ["camera_id"],
            registry=reg,
        )

        # ── Drop counters ──────────────────────────────────────────────────────
        self.dropped_total = Counter(
            f"{_NS}_dropped_total",
            "Tổng số frames/events bị drop theo lý do",
            ["camera_id", "component", "drop_reason"],
            registry=reg,
        )

        # ── Ingress timeout count ──────────────────────────────────────────────
        self.ingress_timeout_total = Counter(
            f"{_NS}_ingress_timeout_total",
            "Số lần send timeout khi đẩy frame vào ZMQ",
            ["camera_id", "drop_reason"],
            registry=reg,
        )

        # ── Egress JSON write rate ─────────────────────────────────────────────
        self.egress_json_rate = Gauge(
            f"{_NS}_egress_json_rate",
            "Tốc độ ghi JSON events (events/sec)",
            ["camera_id"],
            registry=reg,
        )

        # ── Egress writer queue depth ──────────────────────────────────────────
        self.egress_writer_queue_depth = Gauge(
            f"{_NS}_egress_writer_queue_depth",
            "Số tasks trong queue ghi JSON/disk",
            ["camera_id"],
            registry=reg,
        )

        # ── AI Core inference latency (Histogram P50/P95) ──────────────────────
        self.aicore_inference_ms = Histogram(
            f"{_NS}_aicore_inference_ms",
            "Inference latency theo model (ms)",
            ["camera_id", "model"],
            buckets=[5, 10, 20, 50, 100, 200, 500, 1000],
            registry=reg,
        )

        # ── LPR specific ───────────────────────────────────────────────────────
        self.lpr_ocr_total = Counter(
            f"{_NS}_lpr_ocr_total",
            "Tổng số OCR attempts theo kết quả",
            ["camera_id", "result"],
            registry=reg,
        )

        self.lpr_plate_detected = Counter(
            f"{_NS}_lpr_plate_detected_total",
            "Số biển số được phát hiện",
            ["camera_id", "plate_category"],
            registry=reg,
        )

        # ── FR specific ────────────────────────────────────────────────────────
        self.fr_recognition_total = Counter(
            f"{_NS}_fr_recognition_total",
            "Tổng số nhận diện khuôn mặt theo kết quả",
            ["camera_id", "result"],   # result: known|stranger|spoof|low_quality
            registry=reg,
        )

        self.fr_cache_hits = Counter(
            f"{_NS}_fr_cache_hits_total",
            "Số lần hit cache L1/L2 trong FR pipeline",
            ["camera_id", "cache_tier"],  # cache_tier: l1|l2
            registry=reg,
        )

        # ── Alert / Business ───────────────────────────────────────────────────
        self.alerts_sent_total = Counter(
            f"{_NS}_alerts_sent_total",
            "Số alerts đã gửi theo kênh",
            ["channel"],   # channel: telegram|webhook
            registry=reg,
        )

        self.alerts_throttled_total = Counter(
            f"{_NS}_alerts_throttled_total",
            "Số alerts bị throttle do rate limit",
            ["camera_id"],
            registry=reg,
        )

        # ── EOS Storm ─────────────────────────────────────────────────────────
        self.eos_storm_detected = Counter(
            f"{_NS}_eos_storm_detected_total",
            "Số lần EOS storm được phát hiện và guard",
            ["camera_id"],
            registry=reg,
        )

        # ── EOS Events (mới cho seq_id debugging) ────────────────────────────────
        self.eos_received_total = Counter(
            f"{_NS}_eos_received_total",
            "Tổng số EOS events nhận được từ RTSP",
            ["camera_id"],
            registry=reg,
        )

        self.eos_forwarded_total = Counter(
            f"{_NS}_eos_forwarded_total",
            "Tổng số EOS được forward vào ZMQ (không bị guard drop)",
            ["camera_id"],
            registry=reg,
        )

        self.eos_dropped_total = Counter(
            f"{_NS}_eos_dropped_total",
            "Tổng số EOS bị guard drop trong EOS storm",
            ["camera_id"],
            registry=reg,
        )

        # ── RTSP Disconnect Events ──────────────────────────────────────────────
        self.rtsp_disconnect_total = Counter(
            f"{_NS}_rtsp_disconnect_total",
            "Số lần RTSP stream bị ngắt (không tính planned stop)",
            ["camera_id", "reason"],
            registry=reg,
        )

        self.rtsp_reconnect_total = Counter(
            f"{_NS}_rtsp_reconnect_total",
            "Số lần reconnect RTSP thành công",
            ["camera_id"],
            registry=reg,
        )

        # ── Watchdog ──────────────────────────────────────────────────────────
        self.watchdog_restarts_total = Counter(
            f"{_NS}_watchdog_restarts_total",
            "Số lần watchdog trigger restart",
            ["component"],
            registry=reg,
        )

        self.watchdog_circuit_open = Gauge(
            f"{_NS}_watchdog_circuit_open",
            "1 nếu circuit breaker đang open (quá nhiều restart), 0 nếu closed",
            ["component"],
            registry=reg,
        )

    def __getattr__(self, name):
        """Trả về no-op nếu prometheus_client không cài hoặc metric chưa định nghĩa."""
        if self.__dict__.get("_noop"):
            return _NoOpMetric()
        raise AttributeError(f"SvProMetrics has no attribute '{name}'")


# ── Singleton Instance ─────────────────────────────────────────────────────────
metrics = SvProMetrics() if _PROMETHEUS_AVAILABLE else _NoOpMetrics()


# ── HTTP Server (expose /metrics endpoint) ────────────────────────────────────

_server_started = False
_server_lock    = threading.Lock()


def start_metrics_server(port: int = 9100) -> None:
    """
    Khởi động HTTP server để Prometheus scrape /metrics.
    Gọi 1 lần khi pipeline start (idempotent — chỉ start 1 lần).

    Args:
        port: Port để expose metrics. Mặc định 9100.
    """
    global _server_started
    if not _PROMETHEUS_AVAILABLE:
        logger.warning("prometheus_client not available, metrics server not started")
        return
    with _server_lock:
        if _server_started:
            return
        try:
            start_http_server(port)
            _server_started = True
            logger.info("SV-PRO metrics server started on :%d/metrics", port)
        except OSError as exc:
            logger.error("Could not start metrics server on port %d: %s", port, exc)


def record_drop(camera_id: str, component: str, reason: str, count: int = 1) -> None:
    """
    Tiện ích ghi nhận drop event — áp dụng cho bất kỳ component nào.

    Args:
        camera_id: ID camera gây ra drop.
        component: ingress | aicore | json_egress | video_egress
        reason: Mã drop reason từ DropReason constants.
        count: Số frames bị drop (mặc định 1).
    """
    try:
        metrics.dropped_total.labels(
            camera_id=camera_id,
            component=component,
            drop_reason=reason,
        ).inc(count)
    except Exception:
        pass  # Never let telemetry crash the pipeline
