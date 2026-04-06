"""
Access Control — Sprint 4 — Task 4.4.

Kiểm soát truy cập cửa thông qua HTTP relay hoặc GPIO:
  - Trigger mở cửa sau khi Face Recognition pass + Liveness pass + Zone allowed.
  - Ghi log mọi lần trigger vào bảng access_events.
  - Rate limit: tối đa 1 lần mở cửa / người / 10 giây (chống loop).
  - Timeout HTTP relay: 3 giây (fail-fast để không block pipeline).
  - Hỗ trợ nhiều door endpoint: cấu hình trong config/doors.yml.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Timezone Việt Nam ───────────────────────────────────────────────────────────
_VN_TZ = timezone(timedelta(hours=7))

# ── Rate limit mở cửa ──────────────────────────────────────────────────────────
_OPEN_COOLDOWN_SECS = 10.0    # Mỗi người tối đa 1 lần mở/10 giây

# ── Timeout HTTP relay ─────────────────────────────────────────────────────────
_HTTP_TIMEOUT_SECS = 3.0

# ── Trạng thái mở cửa ─────────────────────────────────────────────────────────
_DOOR_OPEN_MS = 5000           # Thời gian giữ cửa mở (ms) — gửi lên relay


@dataclass
class DoorConfig:
    """Cấu hình một cửa trong hệ thống."""
    door_id:     str
    name:        str
    relay_url:   str            # URL HTTP relay controller (e.g. http://192.168.1.10/relay/1)
    zone:        str            # Zone mà cửa này thuộc về (khớp với camera zone)
    open_ms:     int = _DOOR_OPEN_MS
    enabled:     bool = True


@dataclass
class DoorEvent:
    """
    Ghi nhận một lần trigger cửa (mở hoặc từ chối).
    Được trả về bởi AccessController.trigger() để ghi log DB.
    """
    door_id:     str
    person_id:   str
    person_name: str
    person_role: str
    camera_id:   str
    source_id:   str
    granted:     bool           # True = mở cửa, False = từ chối
    reason:      str            # Lý do chi tiết
    timestamp:   str            # ISO 8601 VN timezone
    latency_ms:  float = 0.0   # Thời gian relay phản hồi (ms)
    extra:       dict = field(default_factory=dict)


class AccessController:
    """
    Điều khiển mở cửa thông minh tích hợp vào pipeline FR của SV-PRO.

    Luồng xử lý:
      1. FR pipeline gọi .can_open(person_id, zone, ...)   — kiểm tra điều kiện.
      2. Nếu pass → gọi .trigger(door_id, person_id, ...)  — gửi lệnh HTTP relay.
      3. Trả về DoorEvent để audit_logger ghi lại.

    Sử dụng như singleton: import `access_controller` từ bất kỳ module nào.
    Gọi .initialize() 1 lần khi pipeline start.
    """

    def __init__(self):
        # {door_id: DoorConfig}
        self._doors: dict[str, DoorConfig] = {}
        # Rate limit: {person_id: last_open_monotonic}
        self._open_ts: dict[str, float] = {}
        self._initialized = False

    def initialize(self, doors: list[dict]) -> None:
        """
        Khởi tạo danh sách cửa từ cấu hình (thường đọc từ config/doors.yml).
        Gọi 1 lần khi pipeline start.

        Args:
            doors: Danh sách dict, mỗi dict chứa các field của DoorConfig.
        """
        self._doors.clear()
        for d in doors:
            cfg = DoorConfig(
                door_id   = d["door_id"],
                name      = d.get("name", d["door_id"]),
                relay_url = d["relay_url"],
                zone      = d.get("zone", ""),
                open_ms   = d.get("open_ms", _DOOR_OPEN_MS),
                enabled   = d.get("enabled", True),
            )
            self._doors[cfg.door_id] = cfg
        self._initialized = True
        logger.info("AccessController initialized with %d door(s): %s",
                    len(self._doors), list(self._doors))

    def initialize_simple(self, door_relay_map: dict[str, str]) -> None:
        """
        Phiên bản đơn giản: chỉ truyền {door_id: relay_url}.
        Phù hợp cho dev/test không cần file YAML.
        """
        self.initialize([
            {"door_id": did, "relay_url": url}
            for did, url in door_relay_map.items()
        ])

    # ──────────────────────────────────────────────────────────────────────────
    # Kiểm tra điều kiện (pre-check)
    # ──────────────────────────────────────────────────────────────────────────

    def can_open(
        self,
        person_id:    str,
        person_role:  str,
        door_id:      str,
        liveness_ok:  bool = True,
        zone_allowed: bool = True,
    ) -> tuple[bool, str]:
        """
        Kiểm tra xem một người có được phép mở cửa không.
        Thứ tự kiểm tra:
          1. AccessController đã khởi tạo chưa.
          2. door_id có tồn tại và enabled không.
          3. Liveness detection pass.
          4. Zone access pass.
          5. Rate limit: không mở lại trong _OPEN_COOLDOWN_SECS giây.

        Trả về (allowed: bool, reason: str).
        """
        if not self._initialized:
            return False, "AccessController chưa được khởi tạo"

        door = self._doors.get(door_id)
        if not door:
            return False, f"Cửa '{door_id}' không tồn tại trong cấu hình"
        if not door.enabled:
            return False, f"Cửa '{door_id}' đang bị vô hiệu hóa"

        if not liveness_ok:
            return False, "Phát hiện giả mạo khuôn mặt — từ chối truy cập"

        if not zone_allowed:
            return False, f"Người dùng role='{person_role}' không có quyền vào zone cửa này"

        # Rate limit
        last_open = self._open_ts.get(person_id, 0.0)
        elapsed = time.monotonic() - last_open
        if elapsed < _OPEN_COOLDOWN_SECS:
            remaining = _OPEN_COOLDOWN_SECS - elapsed
            return False, f"Rate limit: đã mở cửa gần đây, chờ thêm {remaining:.1f}s"

        return True, "OK"

    # ──────────────────────────────────────────────────────────────────────────
    # Trigger mở cửa
    # ──────────────────────────────────────────────────────────────────────────

    def trigger(
        self,
        door_id:     str,
        person_id:   str,
        person_name: str,
        person_role: str,
        camera_id:   str,
        source_id:   str,
        liveness_ok:  bool = True,
        zone_allowed: bool = True,
    ) -> DoorEvent:
        """
        Thực hiện mở cửa:
          1. Kiểm tra điều kiện qua can_open().
          2. Nếu pass → gửi HTTP request đến relay controller.
          3. Cập nhật rate limit.
          4. Trả về DoorEvent để caller ghi log DB.

        Gọi từ FaceRecognizer.process_frame() sau khi match thành công.
        """
        ts = datetime.now(_VN_TZ).isoformat()
        allowed, reason = self.can_open(
            person_id, person_role, door_id, liveness_ok, zone_allowed
        )

        if not allowed:
            logger.info(
                "Door DENIED: door=%s person=%s reason=%s",
                door_id, person_id, reason,
            )
            return DoorEvent(
                door_id     = door_id,
                person_id   = person_id,
                person_name = person_name,
                person_role = person_role,
                camera_id   = camera_id,
                source_id   = source_id,
                granted     = False,
                reason      = reason,
                timestamp   = ts,
            )

        door = self._doors[door_id]
        latency_ms, http_ok, http_msg = self._send_relay(door)

        if http_ok:
            self._open_ts[person_id] = time.monotonic()
            logger.info(
                "Door OPENED: door=%s person=%s name=%s latency=%.1fms",
                door_id, person_id, person_name, latency_ms,
            )
        else:
            logger.error(
                "Door relay FAILED: door=%s url=%s error=%s",
                door_id, door.relay_url, http_msg,
            )

        return DoorEvent(
            door_id     = door_id,
            person_id   = person_id,
            person_name = person_name,
            person_role = person_role,
            camera_id   = camera_id,
            source_id   = source_id,
            granted     = http_ok,
            reason      = "Mở cửa thành công" if http_ok else f"Relay lỗi: {http_msg}",
            timestamp   = ts,
            latency_ms  = latency_ms,
            extra       = {
                "door_name": door.name,
                "zone":      door.zone,
                "relay_url": door.relay_url,
            },
        )

    # ──────────────────────────────────────────────────────────────────────────
    # HTTP Relay
    # ──────────────────────────────────────────────────────────────────────────

    def _send_relay(self, door: DoorConfig) -> tuple[float, bool, str]:
        """
        Gửi lệnh mở cửa tới HTTP relay controller.
        Payload JSON: {"action": "open", "duration_ms": <open_ms>}.
        Trả về (latency_ms: float, success: bool, message: str).
        """
        payload = json.dumps({
            "action":      "open",
            "duration_ms": door.open_ms,
            "door_id":     door.door_id,
        }).encode("utf-8")

        start = time.monotonic()
        try:
            req = urllib.request.Request(
                door.relay_url,
                data    = payload,
                headers = {"Content-Type": "application/json"},
                method  = "POST",
            )
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECS) as resp:
                latency_ms = (time.monotonic() - start) * 1000
                status = resp.status
                if 200 <= status < 300:
                    return latency_ms, True, "OK"
                return latency_ms, False, f"HTTP {status}"
        except urllib.error.URLError as exc:
            latency_ms = (time.monotonic() - start) * 1000
            return latency_ms, False, str(exc.reason)
        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            return latency_ms, False, str(exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Tiện ích
    # ──────────────────────────────────────────────────────────────────────────

    def get_door(self, door_id: str) -> Optional[DoorConfig]:
        """Trả về DoorConfig nếu tồn tại, ngược lại None."""
        return self._doors.get(door_id)

    def list_doors(self) -> list[DoorConfig]:
        """Danh sách tất cả cửa đã cấu hình."""
        return list(self._doors.values())

    def set_door_enabled(self, door_id: str, enabled: bool) -> bool:
        """
        Bật/tắt cửa theo yêu cầu vận hành (ví dụ: cúp điện relay).
        Trả về True nếu thành công, False nếu door_id không tồn tại.
        """
        door = self._doors.get(door_id)
        if not door:
            return False
        door.enabled = enabled
        logger.info("Door '%s' %s", door_id, "enabled" if enabled else "disabled")
        return True


# ── Singleton instance ────────────────────────────────────────────────────────
# Import và dùng `access_controller` ở bất kỳ module nào trong dự án.
access_controller = AccessController()
