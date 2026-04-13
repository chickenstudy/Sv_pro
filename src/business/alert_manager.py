"""
Alert Manager cho SV-PRO — Sprint 4 (Refactored Sprint 5).

Xử lý gửi cảnh báo đến Telegram và Webhook khi nhận BlacklistEvent từ BlacklistEngine.

Tính năng:
  - Rate limiting: chặn spam (1 alert / entity / 5 phút theo cấu hình).
  - Gửi ảnh kèm theo qua Telegram sendPhoto nếu có crop.
  - Gửi POST JSON tới danh sách Webhook URLs.
  - Background queue: không block pipeline hot path khi gửi.
  - Retry tối đa 3 lần với exponential backoff khi timeout.
  - Dùng httpx thay urllib thô — gọn, đúng chuẩn, dễ test.
"""

import logging
import queue
import threading
import time
from typing import Optional

import httpx
import numpy as np
import cv2

from .blacklist_engine import BlacklistEvent, Severity

logger = logging.getLogger(__name__)

# ── Timezone Việt Nam ───────────────────────────────────────────────────────────
# (dùng trong build_telegram_text)
from datetime import timezone, timedelta
_VN_TZ = timezone(timedelta(hours=7))

# ── Rate limit mặc định ────────────────────────────────────────────────────────
_DEFAULT_RATE_SECS = 300.0     # 5 phút giữa 2 alert cùng entity
_DEFAULT_GLOBAL_RPM = 20       # Tối đa 20 alert/phút toàn hệ thống

# ── Retry config ────────────────────────────────────────────────────────────────
_MAX_RETRIES      = 3
_RETRY_BASE_DELAY = 1.0        # Giây, nhân đôi mỗi lần retry

# ── Queue size ─────────────────────────────────────────────────────────────────
_QUEUE_MAXSIZE = 500

# ── HTTP timeout (giây) ────────────────────────────────────────────────────────
_TELEGRAM_TIMEOUT = 10.0
_WEBHOOK_TIMEOUT  = 5.0


class AlertManager:
    """
    Quản lý gửi cảnh báo bất đồng bộ (background thread) khi BlacklistEvent xảy ra.

    Sử dụng như singleton: import `alert_manager` ở bất kỳ module nào,
    gọi .initialize() 1 lần khi pipeline start, sau đó .send_alert() mỗi khi cần.
    """

    def __init__(self):
        self._telegram_token: str | None = None
        self._telegram_chat_id: str | None = None
        self._telegram_send_photo: bool = True

        self._webhook_urls: list[str] = []
        self._webhook_headers: dict[str, str] = {"Content-Type": "application/json"}

        self._rate_secs: float = _DEFAULT_RATE_SECS
        self._global_rpm: int  = _DEFAULT_GLOBAL_RPM

        # Rate limiting state
        # Key: (entity_id, event_type) → last_sent_monotonic
        self._rate_map: dict[tuple, float] = {}
        # Đếm số alert đã gửi trong phút hiện tại
        self._minute_count: int  = 0
        self._minute_start: float = 0.0

        # Background worker
        self._queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._worker_thread: threading.Thread | None = None
        self._initialized = False

        # httpx Client dùng chung trong background thread (không thread-safe nếu dùng từ nhiều thread)
        # Worker thread là đơn luồng nên dùng 1 client là an toàn
        self._http: httpx.Client | None = None

    def initialize(
        self,
        telegram_token: str | None = None,
        telegram_chat_id: str | None = None,
        telegram_send_photo: bool = True,
        telegram_timeout: float = _TELEGRAM_TIMEOUT,
        webhook_urls: list[str] | None = None,
        webhook_timeout: float = _WEBHOOK_TIMEOUT,
        webhook_headers: dict | None = None,
        rate_secs: float = _DEFAULT_RATE_SECS,
        global_rpm: int  = _DEFAULT_GLOBAL_RPM,
    ) -> None:
        """
        Khởi tạo AlertManager với cấu hình Telegram và Webhook.
        Gọi 1 lần khi pipeline start (thường từ plugin on_start()).
        """
        self._telegram_token     = telegram_token
        self._telegram_chat_id   = telegram_chat_id
        self._telegram_send_photo = telegram_send_photo
        self._webhook_urls       = webhook_urls or []
        self._webhook_headers    = webhook_headers or {"Content-Type": "application/json"}
        self._rate_secs          = rate_secs
        self._global_rpm         = global_rpm

        self._minute_start = time.monotonic()

        # Tạo httpx Client với timeout hợp lý
        self._http = httpx.Client(
            timeout=httpx.Timeout(
                connect=5.0,
                read=max(telegram_timeout, webhook_timeout),
                write=max(telegram_timeout, webhook_timeout),
                pool=5.0,
            ),
            follow_redirects=True,
        )

        # Khởi động background worker
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="alert-manager",
        )
        self._worker_thread.start()
        self._initialized = True

        tg_ok = bool(self._telegram_token and self._telegram_chat_id)
        logger.info(
            "AlertManager initialized. Telegram: %s, Webhooks: %d, Rate: %.0fs/entity",
            "✅" if tg_ok else "❌ (no token/chat_id)",
            len(self._webhook_urls),
            self._rate_secs,
        )

    def send_alert(self, event: BlacklistEvent, image: Optional[np.ndarray] = None) -> bool:
        """
        Đẩy BlacklistEvent vào hàng đợi gửi bất đồng bộ.
        Kiểm tra rate limit trước khi enqueue (nếu bị rate-limit → bỏ qua).
        Trả về True nếu đã enqueue thành công.
        """
        if not self._initialized:
            logger.warning("AlertManager chưa được initialize — bỏ qua alert.")
            return False

        if not self._check_rate(event):
            logger.debug(
                "Alert bị throttle: entity=%s type=%s",
                event.entity_id, event.event_type,
            )
            return False

        try:
            self._queue.put_nowait((event, image))
            return True
        except queue.Full:
            logger.warning("Alert queue đầy — dropped event %s", event.event_type)
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # Rate limiting
    # ──────────────────────────────────────────────────────────────────────────

    def _check_rate(self, event: BlacklistEvent) -> bool:
        """
        Kiểm tra rate limit per-entity và global rpm.
        Trả về True nếu được phép gửi, False nếu bị chặn.
        """
        now = time.monotonic()

        # Global RPM check
        if now - self._minute_start >= 60.0:
            self._minute_start = now
            self._minute_count = 0
        if self._minute_count >= self._global_rpm:
            return False

        # Per-entity rate check
        rate_key = (event.entity_id, event.event_type)
        last_sent = self._rate_map.get(rate_key, 0.0)
        if now - last_sent < self._rate_secs:
            return False

        # Cập nhật rate map
        self._rate_map[rate_key] = now
        self._minute_count += 1

        # Dọn dẹp rate_map định kỳ (tránh tích lũy vô hạn)
        if len(self._rate_map) > 5000:
            cutoff = now - self._rate_secs * 2
            self._rate_map = {k: v for k, v in self._rate_map.items() if v > cutoff}

        return True

    # ──────────────────────────────────────────────────────────────────────────
    # Background worker
    # ──────────────────────────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        """
        Vòng lặp background: liên tục lấy task từ queue và gửi đi.
        Chạy suốt vòng đời pipeline (daemon thread — tự dừng khi main process kết thúc).
        """
        while True:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                event, image = item
                self._dispatch(event, image)
            except Exception as exc:
                logger.error("Alert dispatch error: %s", exc, exc_info=True)
            finally:
                self._queue.task_done()

    def _dispatch(self, event: BlacklistEvent, image: Optional[np.ndarray]) -> None:
        """
        Gửi alert tới tất cả kênh đã cấu hình (Telegram + Webhooks).
        Chạy trong background worker thread.
        """
        # Encode ảnh thành JPEG bytes nếu có
        img_bytes: bytes | None = None
        if image is not None:
            try:
                ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    img_bytes = buf.tobytes()
            except Exception as exc:
                logger.debug("Encode alert image failed: %s", exc)

        # Gửi Telegram
        if self._telegram_token and self._telegram_chat_id:
            self._send_telegram(event, img_bytes)

        # Gửi Webhooks
        for url in self._webhook_urls:
            self._send_webhook(url, event)

    # ──────────────────────────────────────────────────────────────────────────
    # Telegram — dùng httpx thay urllib
    # ──────────────────────────────────────────────────────────────────────────

    def _build_telegram_text(self, event: BlacklistEvent) -> str:
        """Dịch BlacklistEvent thành text Markdown cho Telegram."""
        severity_icon = {
            Severity.LOW:      "🟡",
            Severity.MEDIUM:   "🟠",
            Severity.HIGH:     "🔴",
            Severity.CRITICAL: "💀",
        }.get(event.severity, "⚠️")

        event_labels = {
            "blacklist_person":     f"{severity_icon} *Phát hiện đối tượng trong danh sách chú ý!*",
            "blacklist_vehicle":    f"{severity_icon} *Phát hiện xe trong danh sách chú ý!*",
            "zone_denied":          f"{severity_icon} *Truy cập trái phép vào khu vực hạn chế!*",
            "time_denied":          f"{severity_icon} *Truy cập ngoài giờ quy định!*",
            "spoof_detected":       "🔴 *Phát hiện giả mạo khuôn mặt!*",
            "stranger_restricted":  "⚠️ *Người lạ trong khu vực hạn chế*",
        }
        header = event_labels.get(event.event_type, "⚠️ *Cảnh báo SV-PRO*")

        lines = [
            header,
            f"📷 Camera: `{event.camera_id}`",
            f"🕐 Thời gian: `{event.timestamp}`",
        ]

        if event.entity_type == "person":
            lines.append(f"👤 Tên: `{event.entity_name}` — ID: `{event.entity_id}`")
        else:
            lines.append(f"🚘 Biển số: `{event.entity_name}`")

        if event.reason:
            lines.append(f"📝 Lý do: _{event.reason}_")

        zone = event.extra.get("zone")
        if zone:
            lines.append(f"📍 Khu vực: `{zone}`")

        return "\n".join(lines)

    def _send_telegram(self, event: BlacklistEvent, img_bytes: bytes | None) -> None:
        """
        Gửi cảnh báo qua Telegram API dùng httpx.
        Tự động chọn sendPhoto (nếu có ảnh) hoặc sendMessage.
        Retry tối đa _MAX_RETRIES lần với exponential backoff.
        """
        base_url = f"https://api.telegram.org/bot{self._telegram_token}"
        text = self._build_telegram_text(event)
        use_photo = self._telegram_send_photo and img_bytes is not None

        for attempt in range(_MAX_RETRIES):
            try:
                if use_photo:
                    self._http.post(
                        f"{base_url}/sendPhoto",
                        data={"chat_id": self._telegram_chat_id, "caption": text, "parse_mode": "Markdown"},
                        files={"photo": ("alert.jpg", img_bytes, "image/jpeg")},
                    ).raise_for_status()
                else:
                    self._http.post(
                        f"{base_url}/sendMessage",
                        json={"chat_id": self._telegram_chat_id, "text": text, "parse_mode": "Markdown"},
                    ).raise_for_status()
                return  # Thành công
            except Exception as exc:
                wait = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Telegram send attempt %d/%d failed (%s) — retrying in %.1fs",
                    attempt + 1, _MAX_RETRIES, exc, wait,
                )
                time.sleep(wait)

        logger.error("Telegram send FAILED after %d attempts for event %s", _MAX_RETRIES, event.event_type)

    # ──────────────────────────────────────────────────────────────────────────
    # Webhook — dùng httpx thay urllib
    # ──────────────────────────────────────────────────────────────────────────

    def _send_webhook(self, url: str, event: BlacklistEvent) -> None:
        """
        Gửi POST JSON tới webhook URL dùng httpx.
        Retry tối đa _MAX_RETRIES lần với exponential backoff.
        """
        payload = {
            "event_type":  event.event_type,
            "entity_type": event.entity_type,
            "entity_id":   event.entity_id,
            "entity_name": event.entity_name,
            "severity":    event.severity.value,
            "camera_id":   event.camera_id,
            "source_id":   event.source_id,
            "reason":      event.reason,
            "timestamp":   event.timestamp,
            "extra":       event.extra,
        }

        for attempt in range(_MAX_RETRIES):
            try:
                self._http.post(url, json=payload, headers=self._webhook_headers).raise_for_status()
                return  # Thành công
            except Exception as exc:
                wait = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("Webhook %s attempt %d/%d failed: %s", url, attempt + 1, _MAX_RETRIES, exc)
                time.sleep(wait)

        logger.error("Webhook %s FAILED after %d attempts.", url, _MAX_RETRIES)


# ── Singleton instance ────────────────────────────────────────────────────────
alert_manager = AlertManager()
