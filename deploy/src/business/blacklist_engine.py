"""
Blacklist/Whitelist Engine cho SV-PRO — Sprint 4.

Kiểm tra kết quả nhận diện từ LPR (biển số) và FR (khuôn mặt) theo các rule:
  1. Blacklist người (role = 'blacklist' trong bảng users).
  2. Blacklist xe (vehicles.is_blacklisted = True).
  3. Access Zone: kiểm tra người dùng có quyền vào zone của camera đó không.
  4. Time-based rule: một số zone chỉ mở trong giờ nhất định.

Cache 2 tầng để tránh query DB liên tục:
  - L1 process-local dict (capacity=500, TTL=60s).
  - Redis L2 (TTL=5 phút, prefetch khi startup).

Mỗi khi phát hiện vi phạm → trả về BlacklistEvent để AlertManager xử lý.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# ── Timezone Việt Nam ───────────────────────────────────────────────────────────
_VN_TZ = timezone(timedelta(hours=7))

# ── Cache TTL (giây) ────────────────────────────────────────────────────────────
_L1_BLACKLIST_TTL  = 60.0      # 1 phút — cập nhật tương đối nhanh
_L1_CAPACITY       = 500       # Số entity tối đa trong L1 cache
_REDIS_BL_TTL      = 300       # 5 phút trong Redis


class Severity(Enum):
    """Mức độ nghiêm trọng của sự kiện vi phạm."""
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class BlacklistEvent:
    """
    Đối tượng chứa thông tin về một sự kiện vi phạm blacklist hoặc access control.
    Được trả về bởi BlacklistEngine.check_*() để AlertManager xử lý.
    """
    event_type: str          # "blacklist_person" | "blacklist_vehicle" | "zone_denied" | "time_denied"
    entity_type: str         # "person" | "vehicle"
    entity_id: str           # person_id hoặc plate_number
    entity_name: str         # Tên hoặc biển số để hiển thị
    severity: Severity
    camera_id: str
    source_id: str
    reason: str              # Mô tả lý do vi phạm
    timestamp: str           # ISO 8601 (VN timezone)
    face_crop: Optional[object] = None   # np.ndarray (tùy chọn)
    plate_crop: Optional[object] = None  # np.ndarray (tùy chọn)
    extra: dict = field(default_factory=dict)


class _L1BLCache:
    """
    Cache bộ nhớ nội tại lưu trạng thái blacklist của entity.
    Key: entity_id (person_id hoặc plate_number).
    Value: (is_blacklisted: bool, reason: str, timestamp: float).
    """

    def __init__(self, capacity: int = _L1_CAPACITY, ttl: float = _L1_BLACKLIST_TTL):
        self._data: dict[str, tuple[bool, str, float]] = {}
        self._capacity = capacity
        self._ttl = ttl

    def get(self, entity_id: str) -> Optional[tuple[bool, str]]:
        """Trả về (is_blacklisted, reason) nếu còn TTL, ngược lại None."""
        item = self._data.get(entity_id)
        if item is None:
            return None
        is_bl, reason, ts = item
        if time.monotonic() - ts > self._ttl:
            del self._data[entity_id]
            return None
        return is_bl, reason

    def put(self, entity_id: str, is_blacklisted: bool, reason: str) -> None:
        """Lưu trạng thái blacklist của entity vào cache."""
        # Giới hạn dung lượng đơn giản: xóa entry ngẫu nhiên nếu đầy
        if len(self._data) >= self._capacity and entity_id not in self._data:
            first_key = next(iter(self._data))
            del self._data[first_key]
        self._data[entity_id] = (is_blacklisted, reason, time.monotonic())

    def invalidate(self, entity_id: str) -> None:
        """Xóa khỏi cache khi DB cập nhật mới (VD: thêm xe vào blacklist)."""
        self._data.pop(entity_id, None)


class BlacklistEngine:
    """
    Engine kiểm tra blacklist/whitelist và access control cho SV-PRO.

    Sử dụng như một singleton (1 instance cho toàn process).
    Khởi tạo bằng cách gọi .initialize(db_dsn, redis_client) khi pipeline start.
    Sau đó gọi .check_person() và .check_vehicle() từ pyfunc plugins.
    """

    def __init__(self):
        # Models/connections (khởi tạo sau qua initialize())
        self._db_dsn: str | None = None
        self._redis   = None

        # L1 cache riêng cho người và xe
        self._person_cache  = _L1BLCache()
        self._vehicle_cache = _L1BLCache()

        # Camera → zone mapping (cấu hình từ module.yml hoặc DB)
        self._camera_zones: dict[str, str] = {}

        # Zone access rules: {zone: {role: bool}}
        # True = được phép, False = bị chặn
        self._zone_access: dict[str, list[str]] = {}  # zone → [allowed_roles]

        # Time-based zone rules: {zone: [(start_hour, end_hour)]}
        self._zone_time_rules: dict[str, list[tuple[int, int]]] = {}

        self._initialized = False

    def initialize(
        self,
        db_dsn: str,
        redis_client=None,
        camera_zones: dict | None = None,
        zone_access: dict | None = None,
        zone_time_rules: dict | None = None,
    ) -> None:
        """
        Khởi tạo engine với kết nối DB và Redis.
        Gọi 1 lần khi pipeline start từ FaceRecognizer.on_start() hoặc plugin tương tự.
        """
        self._db_dsn        = db_dsn
        self._redis         = redis_client
        self._camera_zones  = camera_zones or {}
        self._zone_access   = zone_access or {}
        self._zone_time_rules = zone_time_rules or {}
        self._initialized   = True

        if redis_client:
            self._prefetch_blacklist_to_redis()
        logger.info("BlacklistEngine initialized. Camera zones: %s", self._camera_zones)

    def _prefetch_blacklist_to_redis(self) -> None:
        """
        Tải danh sách blacklist từ DB lên Redis khi startup.
        Giúp tra cứu nhanh mà không cần mở kết nối DB mỗi frame.
        """
        if not self._db_dsn or not self._redis:
            return
        try:
            import psycopg2, json
            conn = psycopg2.connect(self._db_dsn)
            cur  = conn.cursor()

            pipe = self._redis.pipeline(transaction=False)
            count = 0
            for uid, name, reason in cur.fetchall():
                key = f"svpro:bl:person:{uid}"
                pipe.setex(key, _REDIS_BL_TTL, json.dumps({"name": name or "", "reason": reason or "blacklisted"}))
                count += 1

            # Blacklist xe
            cur.execute("SELECT plate_number, blacklist_reason FROM vehicles WHERE is_blacklisted = TRUE")
            for plate, reason in cur.fetchall():
                key = f"svpro:bl:vehicle:{plate}"
                pipe.setex(key, _REDIS_BL_TTL, json.dumps({"reason": reason or "blacklisted"}))
                count += 1

            pipe.execute()
            cur.close()
            conn.close()
            logger.info("Prefetched %d blacklist entries → Redis.", count)
        except Exception as exc:
            logger.warning("Blacklist prefetch failed: %s", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # API công khai — được gọi từ pyfunc plugins
    # ──────────────────────────────────────────────────────────────────────────

    def check_person(
        self,
        person_id: str,
        person_name: str,
        person_role: str,
        source_id: str,
        camera_id: str,
        face_crop=None,
    ) -> Optional[BlacklistEvent]:
        """
        Kiểm tra một kết quả nhận diện khuôn mặt có vi phạm rule không.
        Thứ tự kiểm tra: Blacklist → Zone access → Time-based rule.

        Trả về BlacklistEvent nếu có vi phạm, None nếu không.
        """
        ts = datetime.now(_VN_TZ).isoformat()

        # ── 1. Kiểm tra blacklist ───────────────────────────────────────────────
        is_bl, reason = self._is_person_blacklisted(person_id)
        if is_bl:
            return BlacklistEvent(
                event_type  = "blacklist_person",
                entity_type = "person",
                entity_id   = person_id,
                entity_name = person_name,
                severity    = Severity.HIGH,
                camera_id   = camera_id,
                source_id   = source_id,
                reason      = reason,
                timestamp   = ts,
                face_crop   = face_crop,
            )

        # ── 2. Kiểm tra zone access (nếu là stranger hoặc unknown role) ─────────
        zone = self._camera_zones.get(camera_id) or self._camera_zones.get(source_id)
        if zone:
            allowed_roles = self._zone_access.get(zone, [])
            if allowed_roles and person_role not in allowed_roles:
                return BlacklistEvent(
                    event_type  = "zone_denied",
                    entity_type = "person",
                    entity_id   = person_id,
                    entity_name = person_name,
                    severity    = Severity.MEDIUM,
                    camera_id   = camera_id,
                    source_id   = source_id,
                    reason      = f"Role '{person_role}' không được phép vào zone '{zone}'",
                    timestamp   = ts,
                    face_crop   = face_crop,
                    extra       = {"zone": zone, "allowed_roles": allowed_roles},
                )

            # ── 3. Kiểm tra time-based rule ─────────────────────────────────────
            time_event = self._check_time_rule(zone, person_id, person_name, camera_id, source_id, ts)
            if time_event:
                return time_event

        return None

    def check_vehicle(
        self,
        plate_number: str,
        plate_category: str,
        source_id: str,
        camera_id: str,
        plate_crop=None,
    ) -> Optional[BlacklistEvent]:
        """
        Kiểm tra biển số xe có trong blacklist không.
        Trả về BlacklistEvent nếu bị blacklist, None nếu không.
        """
        ts = datetime.now(_VN_TZ).isoformat()

        is_bl, reason = self._is_vehicle_blacklisted(plate_number)
        if is_bl:
            return BlacklistEvent(
                event_type  = "blacklist_vehicle",
                entity_type = "vehicle",
                entity_id   = plate_number,
                entity_name = plate_number,
                severity    = Severity.HIGH,
                camera_id   = camera_id,
                source_id   = source_id,
                reason      = reason,
                timestamp   = ts,
                plate_crop  = plate_crop,
                extra       = {"plate_category": plate_category},
            )
        return None

    # ──────────────────────────────────────────────────────────────────────────
    # Tra cứu nội bộ (L1 → Redis → DB)
    # ──────────────────────────────────────────────────────────────────────────

    def _is_person_blacklisted(self, person_id: str) -> tuple[bool, str]:
        """
        Kiểm tra người theo thứ tự: L1 cache → Redis → DB.
        Trả về (is_blacklisted, reason).
        Cache kết quả vào L1 sau mỗi lần tra.
        """
        # L1
        cached = self._person_cache.get(person_id)
        if cached is not None:
            return cached

        # Redis
        if self._redis:
            try:
                raw = self._redis.get(f"svpro:bl:person:{person_id}")
                if raw:
                    import json
                    data = json.loads(raw)
                    result = (True, data.get("reason", "blacklisted"))
                    self._person_cache.put(person_id, *result)
                    return result
                # Không có trong Redis → không phải blacklist (cache negative)
                self._person_cache.put(person_id, False, "")
                return False, ""
            except Exception as exc:
                logger.debug("Redis bl person check fail: %s", exc)

        # DB fallback
        return self._check_person_db(person_id)

    def _is_vehicle_blacklisted(self, plate_number: str) -> tuple[bool, str]:
        """
        Kiểm tra xe theo thứ tự: L1 cache → Redis → DB.
        Trả về (is_blacklisted, reason).
        """
        cached = self._vehicle_cache.get(plate_number)
        if cached is not None:
            return cached

        if self._redis:
            try:
                raw = self._redis.get(f"svpro:bl:vehicle:{plate_number}")
                if raw:
                    import json
                    data = json.loads(raw)
                    result = (True, data.get("reason", "blacklisted"))
                    self._vehicle_cache.put(plate_number, *result)
                    return result
                self._vehicle_cache.put(plate_number, False, "")
                return False, ""
            except Exception as exc:
                logger.debug("Redis bl vehicle check fail: %s", exc)

        return self._check_vehicle_db(plate_number)

    def _check_person_db(self, person_id: str) -> tuple[bool, str]:
        """Query trực tiếp DB khi L1 và Redis đều miss."""
        if not self._db_dsn:
            return False, ""
        try:
            import psycopg2
            conn = psycopg2.connect(self._db_dsn)
            cur  = conn.cursor()
            cur.execute(
                "SELECT role, blacklist_reason FROM users WHERE id = %s AND active = TRUE",
                (person_id,),
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            if row and row[0] == "blacklist":
                reason = row[1] or "blacklisted"
                self._person_cache.put(person_id, True, reason)
                return True, reason
            self._person_cache.put(person_id, False, "")
            return False, ""
        except Exception as exc:
            logger.debug("DB person blacklist check fail: %s", exc)
            return False, ""

    def _check_vehicle_db(self, plate_number: str) -> tuple[bool, str]:
        """Query trực tiếp DB khi L1 và Redis đều miss."""
        if not self._db_dsn:
            return False, ""
        try:
            import psycopg2
            conn = psycopg2.connect(self._db_dsn)
            cur  = conn.cursor()
            cur.execute(
                "SELECT blacklist_reason FROM vehicles WHERE plate_number = %s AND is_blacklisted = TRUE",
                (plate_number,),
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            if row:
                reason = row[0] or "blacklisted"
                self._vehicle_cache.put(plate_number, True, reason)
                return True, reason
            self._vehicle_cache.put(plate_number, False, "")
            return False, ""
        except Exception as exc:
            logger.debug("DB vehicle blacklist check fail: %s", exc)
            return False, ""

    def _check_time_rule(
        self, zone: str, person_id: str, person_name: str,
        camera_id: str, source_id: str, ts: str,
    ) -> Optional[BlacklistEvent]:
        """
        Kiểm tra xem hiện tại có nằm trong khung giờ cho phép của zone không.
        Nếu ngoài giờ → trả về BlacklistEvent với severity MEDIUM.
        """
        time_rules = self._zone_time_rules.get(zone)
        if not time_rules:
            return None   # Không có rule → cho phép mọi lúc

        now_hour = datetime.now(_VN_TZ).hour
        for start_h, end_h in time_rules:
            if start_h <= now_hour < end_h:
                return None   # Trong giờ cho phép

        return BlacklistEvent(
            event_type  = "time_denied",
            entity_type = "person",
            entity_id   = person_id,
            entity_name = person_name,
            severity    = Severity.MEDIUM,
            camera_id   = camera_id,
            source_id   = source_id,
            reason      = f"Ngoài giờ truy cập zone '{zone}' (hiện tại: {datetime.now(_VN_TZ).strftime('%H:%M')})",
            timestamp   = ts,
            extra       = {"zone": zone, "time_rules": time_rules},
        )

    def invalidate_person(self, person_id: str) -> None:
        """Xóa cache khi DB cập nhật trạng thái blacklist của người này."""
        self._person_cache.invalidate(person_id)
        if self._redis:
            try:
                self._redis.delete(f"svpro:bl:person:{person_id}")
            except Exception:
                pass

    def invalidate_vehicle(self, plate_number: str) -> None:
        """Xóa cache khi DB cập nhật trạng thái blacklist của xe này."""
        self._vehicle_cache.invalidate(plate_number)
        if self._redis:
            try:
                self._redis.delete(f"svpro:bl:vehicle:{plate_number}")
            except Exception:
                pass


# ── Singleton instance ────────────────────────────────────────────────────────
# Import và dùng `blacklist_engine` ở bất kỳ module nào trong dự án.
blacklist_engine = BlacklistEngine()


# ── Savant PyFunc wrapper (used by module/module.yml) ──────────────────────────

try:
    from savant.deepstream.meta.frame import NvDsFrameMeta
    from savant.deepstream.pyfunc import NvDsPyFuncPlugin
except Exception:  # pragma: no cover
    NvDsFrameMeta = object  # type: ignore
    NvDsPyFuncPlugin = object  # type: ignore


class BlacklistPyfunc(NvDsPyFuncPlugin):
    """
    Savant PyFunc stage: chạy business rules (blacklist / zone access / linking / alerts).

    Lưu ý: PyFunc này được tham chiếu trực tiếp trong `module/module.yml`:
      module: src.business.blacklist_engine
      class_name: BlacklistPyfunc
    """

    def __init__(
        self,
        camera_zones: dict | None = None,
        zone_access: dict | None = None,
        zone_time_rules: dict | None = None,
        object_linker_max_dist_px: float = 150.0,
        object_linker_temporal_secs: float = 2.0,
        audit_base_dir: str = "/Detect/audit",
        alert_rate_secs: float = 60.0,
        alert_global_rpm: int = 20,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._camera_zones = camera_zones or {}
        self._zone_access = zone_access or {}
        self._zone_time_rules = zone_time_rules or {}
        self._audit_base_dir = audit_base_dir
        self._alert_rate_secs = alert_rate_secs
        self._alert_global_rpm = alert_global_rpm
        self._link_max_dist_px = object_linker_max_dist_px
        self._link_temporal_secs = object_linker_temporal_secs

        self._object_linker = None
        self._alert_manager = None
        self._audit_logger = None
        self._telemetry = None

    def on_start(self) -> bool:
        if not super().on_start():
            return False

        # Lazy imports: keep the stage resilient in minimal/dev environments.
        try:
            from src.business.object_linker import ObjectLinker
            self._object_linker = ObjectLinker(
                max_pixel_dist=float(self._link_max_dist_px),
                temporal_secs=float(self._link_temporal_secs),
            )
        except Exception as exc:
            logger.warning("ObjectLinker disabled (init failed): %s", exc)
            self._object_linker = None

        try:
            from src.business.alert_manager import alert_manager
            alert_manager.initialize(
                telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
                telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
                webhook_urls=[u for u in [os.environ.get("ALERT_WEBHOOK_URL")] if u],
                rate_secs=float(self._alert_rate_secs),
                global_rpm=int(self._alert_global_rpm),
            )
            self._alert_manager = alert_manager
        except Exception as exc:
            logger.warning("AlertManager disabled (init failed): %s", exc)
            self._alert_manager = None

        try:
            from src.business.audit_logger import audit_logger
            audit_logger.initialize(
                db_dsn=os.environ.get("POSTGRES_DSN"),
                audit_base=self._audit_base_dir,
            )
            self._audit_logger = audit_logger
        except Exception as exc:
            logger.warning("AuditLogger disabled (init failed): %s", exc)
            self._audit_logger = None

        try:
            from src.telemetry import metrics
            self._telemetry = metrics
        except Exception:
            self._telemetry = None

        # Initialize blacklist engine (DB/Redis optional; will degrade gracefully)
        try:
            import redis

            r = None
            try:
                r = redis.Redis(
                    host=os.environ.get("REDIS_HOST", "redis"),
                    port=int(os.environ.get("REDIS_PORT", "6379")),
                    db=int(os.environ.get("REDIS_DB", "0")),
                    socket_connect_timeout=2,
                    decode_responses=False,
                )
                r.ping()
            except Exception:
                r = None

            blacklist_engine.initialize(
                db_dsn=os.environ.get("POSTGRES_DSN", ""),
                redis_client=r,
                camera_zones=self._camera_zones,
                zone_access=self._zone_access,
                zone_time_rules=self._zone_time_rules,
            )
        except Exception as exc:
            logger.warning("BlacklistEngine initialize failed (continuing): %s", exc)

        return True

    def process_frame(self, buffer, frame_meta: NvDsFrameMeta) -> None:
        # This stage should never crash the pipeline.
        try:
            self._process_frame_safe(frame_meta)
        except Exception as exc:
            logger.warning("Business logic error: %s", exc)

    def _process_frame_safe(self, frame_meta: NvDsFrameMeta) -> None:
        source_id = str(getattr(frame_meta, "source_id", "unknown"))
        now = time.monotonic()

        # Collect per-frame observations for optional linking.
        # Note: bounding boxes are optional here; if unavailable, linker may be skipped.
        for obj_meta in getattr(frame_meta, "objects", []):
            # --- LPR observation ---
            plate_attr = None
            try:
                plate_attr = obj_meta.get_attr_meta("lpr", "plate_number")
            except Exception:
                plate_attr = None

            if plate_attr is not None and getattr(plate_attr, "value", None):
                plate_number = str(plate_attr.value)
                plate_category = "UNKNOWN"
                try:
                    cat_attr = obj_meta.get_attr_meta("lpr", "plate_category")
                    if cat_attr is not None and getattr(cat_attr, "value", None):
                        plate_category = str(cat_attr.value)
                except Exception:
                    pass

                # Blacklist vehicle check
                ev = blacklist_engine.check_vehicle(
                    plate_number=plate_number,
                    plate_category=plate_category,
                    source_id=source_id,
                    camera_id=source_id,
                    plate_crop=None,
                )
                if ev:
                    if self._audit_logger:
                        self._audit_logger.log_blacklist_event(ev, plate_crop=None)
                    if self._alert_manager:
                        self._alert_manager.send_alert(ev, image=None)

            # --- FR observation ---
            person_id = None
            try:
                pid_attr = obj_meta.get_attr_meta("fr", "person_id")
                if pid_attr is not None:
                    person_id = str(pid_attr.value)
            except Exception:
                person_id = None

            if person_id:
                try:
                    name_attr = obj_meta.get_attr_meta("fr", "person_name")
                    role_attr = obj_meta.get_attr_meta("fr", "person_role")
                    person_name = str(name_attr.value) if name_attr is not None else ""
                    person_role = str(role_attr.value) if role_attr is not None else "unknown"
                except Exception:
                    person_name, person_role = "", "unknown"

                ev = blacklist_engine.check_person(
                    person_id=person_id,
                    person_name=person_name,
                    person_role=person_role,
                    source_id=source_id,
                    camera_id=source_id,
                    face_crop=None,
                )
                if ev:
                    if self._audit_logger:
                        self._audit_logger.log_blacklist_event(ev, face_crop=None)
                    if self._alert_manager:
                        self._alert_manager.send_alert(ev, image=None)

