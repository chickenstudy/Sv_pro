"""
Audit Logger — Sprint 4 — Ghi nhật ký sự kiện cấp ALERT đầy đủ.

Chức năng:
  Lưu mọi BlacklistEvent và LinkedEvent vào:
    1. File JSON  — thư mục /Detect/audit/YYYY-MM-DD/ (cùng cấu trúc với LPR).
    2. Ảnh crop   — vehicle.jpg, plate.jpg, face.jpg kèm theo file JSON.
    3. PostgreSQL — bảng access_events (bản ghi tóm tắt + path đến file JSON).

Retention:
  - Event thường: 90 ngày.
  - Event ALERT (HIGH/CRITICAL): 1 năm.

Background queue — không block pipeline hot path khi ghi disk hoặc DB.
"""

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import cv2
import numpy as np

from .blacklist_engine import BlacklistEvent, Severity

logger = logging.getLogger(__name__)

# ── Timezone & Đường dẫn ────────────────────────────────────────────────────────
_VN_TZ        = timezone(timedelta(hours=7))
_AUDIT_BASE   = "/Detect/audit"

# ── Retention (ngày) ────────────────────────────────────────────────────────────
_RETENTION_NORMAL_DAYS = 90
_RETENTION_ALERT_DAYS  = 365

# ── Background queue ────────────────────────────────────────────────────────────
_QUEUE_MAXSIZE = 300


class AuditLogger:
    """
    Ghi nhật ký sự kiện bảo mật bất đồng bộ (background thread).

    Sử dụng như singleton. Gọi .initialize() 1 lần rồi dùng:
      .log_blacklist_event(event, face_image, vehicle_image)
      .log_linked_event(linked_event)
    """

    def __init__(self):
        self._db_dsn: str | None = None
        self._audit_base: str    = _AUDIT_BASE
        self._db_pool            = None   # psycopg2 ThreadedConnectionPool

        self._queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._worker: threading.Thread | None = None
        self._initialized = False

    def initialize(self, db_dsn: str | None = None, audit_base: str | None = None) -> None:
        """
        Khởi tạo AuditLogger với kết nối DB (tùy chọn) và đường dẫn thư mục lưu trữ.
        Gọi 1 lần khi pipeline start.
        """
        self._db_dsn      = db_dsn
        self._audit_base  = audit_base or _AUDIT_BASE
        os.makedirs(self._audit_base, exist_ok=True)

        if db_dsn:
            try:
                import psycopg2.pool
                self._db_pool = psycopg2.pool.ThreadedConnectionPool(1, 4, db_dsn)
                logger.info("AuditLogger DB pool created (min=1, max=4).")
            except Exception as exc:
                logger.warning("AuditLogger DB pool init failed: %s — DB writes disabled.", exc)
                self._db_pool = None

        self._worker = threading.Thread(
            target=self._write_loop,
            daemon=True,
            name="audit-logger",
        )
        self._worker.start()
        self._initialized = True
        logger.info("AuditLogger initialized. Base: %s, DB: %s", self._audit_base, "yes" if db_dsn else "no")

    # ──────────────────────────────────────────────────────────────────────────
    # API công khai
    # ──────────────────────────────────────────────────────────────────────────

    def log_blacklist_event(
        self,
        event: BlacklistEvent,
        face_crop:    Optional[np.ndarray] = None,
        vehicle_crop: Optional[np.ndarray] = None,
        plate_crop:   Optional[np.ndarray] = None,
    ) -> None:
        """
        Enqueue ghi nhật ký một BlacklistEvent (người hoặc xe).
        Nếu queue đầy → bỏ qua (không block pipeline).
        """
        if not self._initialized:
            return
        try:
            self._queue.put_nowait((
                "blacklist",
                event,
                face_crop.copy() if face_crop is not None else None,
                vehicle_crop.copy() if vehicle_crop is not None else None,
                plate_crop.copy() if plate_crop is not None else None,
            ))
        except queue.Full:
            logger.warning("AuditLogger queue full — dropped event %s", event.event_type)

    def log_linked_event(self, linked_event: Any) -> None:
        """
        Enqueue ghi nhật ký một LinkedEvent (cặp xe-người đã được ghép).
        Chấp nhận object bất kỳ có thuộc tính .metadata và .linked_at.
        """
        if not self._initialized:
            return
        try:
            self._queue.put_nowait(("linked", linked_event, None, None, None))
        except queue.Full:
            logger.warning("AuditLogger queue full — dropped linked event")

    # ──────────────────────────────────────────────────────────────────────────
    # Background writer
    # ──────────────────────────────────────────────────────────────────────────

    def _write_loop(self) -> None:
        """
        Vòng lặp background: lấy task từ queue và ghi ra disk + DB.
        Chạy suốt vòng đời pipeline.
        """
        while True:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                kind, obj, face_crop, vehicle_crop, plate_crop = item
                if kind == "blacklist":
                    self._write_blacklist(obj, face_crop, vehicle_crop, plate_crop)
                elif kind == "linked":
                    self._write_linked(obj)
            except Exception as exc:
                logger.error("AuditLogger write error: %s", exc, exc_info=True)
            finally:
                self._queue.task_done()

    def _write_blacklist(
        self,
        event: BlacklistEvent,
        face_crop:    Optional[np.ndarray],
        vehicle_crop: Optional[np.ndarray],
        plate_crop:   Optional[np.ndarray],
    ) -> None:
        """
        Ghi BlacklistEvent ra disk (JSON + ảnh) và DB.
        Cấu trúc thư mục: /Detect/audit/YYYY-MM-DD/{event_type}/{timestamp_prefix}/
        """
        now  = datetime.now(_VN_TZ)
        date = now.strftime("%Y-%m-%d")
        ts   = now.strftime("%H%M%S_%f")[:11]   # HHMMSSmmm (11 ký tự)

        save_dir = os.path.join(
            self._audit_base,
            date,
            event.event_type,
            event.source_id,
        )
        os.makedirs(save_dir, exist_ok=True)

        prefix = f"{ts}_{event.entity_id[:8]}"

        # Tạo payload JSON
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
            "has_face_crop":    face_crop is not None,
            "has_vehicle_crop": vehicle_crop is not None,
            "has_plate_crop":   plate_crop is not None,
        }

        json_path = os.path.join(save_dir, f"{prefix}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        # Ghi ảnh
        for img, suffix in [
            (face_crop,    "face.jpg"),
            (vehicle_crop, "vehicle.jpg"),
            (plate_crop,   "plate.jpg"),
        ]:
            if img is not None:
                try:
                    cv2.imwrite(os.path.join(save_dir, f"{prefix}_{suffix}"), img)
                except Exception as exc:
                    logger.debug("Audit image write error (%s): %s", suffix, exc)

        # Ghi DB
        if self._db_dsn:
            self._db_insert_access_event(payload, json_path)

        logger.debug("Audit written: %s", json_path)

        # Thông báo cho watchdog biết pipeline đang hoạt động
        try:
            from src.watchdog.pipeline_watchdog import pipeline_watchdog
            pipeline_watchdog.notify_json_activity(camera_id=payload.get("camera_id", "global"))
        except Exception:
            pass

    def _write_linked(self, linked_event: Any) -> None:
        """
        Ghi LinkedEvent ra disk và DB.
        Lưu vào thư mục audit/YYYY-MM-DD/linked/{source_id}/
        """
        now  = datetime.now(_VN_TZ)
        date = now.strftime("%Y-%m-%d")
        ts   = now.strftime("%H%M%S_%f")[:11]

        source_id    = getattr(linked_event, "source_id", "unknown")
        plate_number = linked_event.metadata.get("plate_number", "UNKNOWN") if hasattr(linked_event, "metadata") else "UNKNOWN"

        save_dir = os.path.join(self._audit_base, date, "linked", source_id)
        os.makedirs(save_dir, exist_ok=True)

        prefix = f"{ts}_{plate_number[:8]}"
        payload = linked_event.metadata if hasattr(linked_event, "metadata") else {}
        payload["linked_at"] = getattr(linked_event, "linked_at", now.isoformat())
        payload["event_type"] = "object_linked"

        json_path = os.path.join(save_dir, f"{prefix}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        # Ảnh từ vehicle và face
        vehicle = getattr(linked_event, "vehicle", None)
        person  = getattr(linked_event, "person", None)
        if vehicle and getattr(vehicle, "plate_crop", None) is not None:
            try:
                cv2.imwrite(os.path.join(save_dir, f"{prefix}_plate.jpg"), vehicle.plate_crop)
            except Exception:
                pass
        if person and getattr(person, "face_crop", None) is not None:
            try:
                cv2.imwrite(os.path.join(save_dir, f"{prefix}_face.jpg"), person.face_crop)
            except Exception:
                pass

        logger.debug("Linked audit written: %s", json_path)

        # Thông báo cho watchdog
        try:
            from src.watchdog.pipeline_watchdog import pipeline_watchdog
            pipeline_watchdog.notify_json_activity(camera_id=source_id)
        except Exception:
            pass

    def _db_insert_access_event(self, payload: dict, json_path: str) -> None:
        """
        Ghi tóm tắt event vào bảng access_events trong PostgreSQL.
        Dùng connection pool để tránh tạo TCP connection mới mỗi event.
        """
        if not self._db_pool:
            return
        conn = None
        try:
            conn = self._db_pool.getconn()
            cur  = conn.cursor()
            cur.execute(
                """
                INSERT INTO access_events
                  (event_type, entity_type, entity_id, severity,
                   camera_id, source_id, reason, event_timestamp, json_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    payload["event_type"],
                    payload["entity_type"],
                    payload["entity_id"],
                    payload["severity"],
                    payload["camera_id"],
                    payload["source_id"],
                    payload["reason"],
                    payload["timestamp"],
                    json_path,
                ),
            )
            conn.commit()
            cur.close()
        except Exception as exc:
            logger.debug("DB access_events insert error: %s", exc)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn:
                self._db_pool.putconn(conn)


# ── Singleton instance ────────────────────────────────────────────────────────
audit_logger = AuditLogger()
