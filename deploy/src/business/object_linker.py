"""
Object Linker — Sprint 4 — Liên kết biển số xe với khuôn mặt người đi cùng.

Logic:
  Sau khi LPR nhận diện được biển số và FR nhận diện được khuôn mặt trong cùng frame,
  ObjectLinker kiểm tra:
    1. Spatial proximity: tâm bounding box xe và người cách nhau < max_pixel_dist px.
    2. Temporal window: sự kiện phải xảy ra trong khoảng thời gian nhỏ hơn temporal_secs.

  Nếu thỏa → tạo LinkedEvent chứa cả 2 kết quả để ghi vào DB và gửi alert tổng hợp.

  Bộ đệm theo source_id để xử lý nhiều camera song song mà không block nhau.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Timezone Việt Nam ───────────────────────────────────────────────────────────
_VN_TZ = timezone(timedelta(hours=7))

# ── Mặc định cấu hình ─────────────────────────────────────────────────────────
_DEFAULT_MAX_DIST_PX   = 150.0   # Khoảng cách pixel tối đa giữa tâm xe và người
_DEFAULT_TEMPORAL_SECS = 2.0     # Cửa sổ thời gian (giây) để ghép nối
_BUFFER_MAX_AGE        = 5.0     # Xóa buffer cũ hơn 5 giây


@dataclass
class VehicleObservation:
    """Kết quả nhận diện xe (từ LPR pipeline) chờ ghép với người."""
    plate_number:   str
    plate_category: str
    bbox:           tuple[int, int, int, int]   # (x1, y1, x2, y2) trên frame
    source_id:      str
    camera_id:      str
    timestamp:      float           # time.monotonic()
    plate_crop:     Optional[object] = None  # np.ndarray hoặc None


@dataclass
class PersonObservation:
    """Kết quả nhận diện người (từ FR pipeline) chờ ghép với xe."""
    person_id:      str
    person_name:    str
    person_role:    str
    fr_confidence:  float
    bbox:           tuple[int, int, int, int]   # (x1, y1, x2, y2) trên frame
    source_id:      str
    camera_id:      str
    timestamp:      float           # time.monotonic()
    face_crop:      Optional[object] = None  # np.ndarray hoặc None
    is_stranger:    bool = False


@dataclass
class LinkedEvent:
    """
    Sự kiện đã được ghép nối giữa xe và người.
    Được lưu vào DB và có thể trigger alert nghiệp vụ.
    """
    vehicle:    VehicleObservation
    person:     PersonObservation
    distance_px: float
    source_id:   str
    camera_id:   str
    linked_at:   str   # ISO 8601 timestamp (VN timezone)
    metadata:    dict = field(default_factory=dict)


class ObjectLinker:
    """
    Ghép nối kết quả nhận diện xe (LPR) và người (FR) trong cùng frame/camera.

    Cách dùng:
      1. Khi LPR nhận diện được biển số → gọi .add_vehicle(obs).
      2. Khi FR nhận diện được mặt      → gọi .add_person(obs).
      3. Mỗi lần add, ObjectLinker tự động tìm cặp phù hợp và trả về LinkedEvent nếu có.

    Dữ liệu buffer tự động hết hạn sau _BUFFER_MAX_AGE giây.
    """

    def __init__(
        self,
        max_pixel_dist: float = _DEFAULT_MAX_DIST_PX,
        temporal_secs:  float = _DEFAULT_TEMPORAL_SECS,
    ):
        self._max_dist    = max_pixel_dist
        self._temporal    = temporal_secs

        # Lưu các observation đang chờ ghép theo source_id
        # {source_id: [VehicleObservation, ...]}
        self._vehicle_buf: dict[str, list[VehicleObservation]] = {}
        # {source_id: [PersonObservation, ...]}
        self._person_buf:  dict[str, list[PersonObservation]]  = {}

        # Lưu các cặp đã ghép thành công để tránh ghép lại (dedup)
        # Key: (plate_number, person_id) → last_linked_monotonic
        self._linked_pairs: dict[tuple, float] = {}
        self._dedup_secs = 30.0   # Không ghép lại cùng cặp trong 30 giây

    def add_vehicle(self, obs: VehicleObservation) -> Optional[LinkedEvent]:
        """
        Thêm observation xe mới vào buffer. Ngay lập tức tìm kiếm người phù hợp đang chờ.
        Trả về LinkedEvent nếu ghép được, None nếu không tìm thấy cặp phù hợp.
        """
        self._flush_expired(obs.source_id, obs.timestamp)

        buf = self._vehicle_buf.setdefault(obs.source_id, [])
        buf.append(obs)

        return self._try_link_vehicle(obs)

    def add_person(self, obs: PersonObservation) -> Optional[LinkedEvent]:
        """
        Thêm observation người mới vào buffer. Ngay lập tức tìm kiếm xe phù hợp đang chờ.
        Trả về LinkedEvent nếu ghép được.
        """
        self._flush_expired(obs.source_id, obs.timestamp)

        buf = self._person_buf.setdefault(obs.source_id, [])
        buf.append(obs)

        return self._try_link_person(obs)

    # ──────────────────────────────────────────────────────────────────────────
    # Tìm kiếm cặp phù hợp
    # ──────────────────────────────────────────────────────────────────────────

    def _try_link_vehicle(self, vehicle: VehicleObservation) -> Optional[LinkedEvent]:
        """
        Với một xe vừa thêm vào, tìm người đang chờ trong cùng source_id
        thỏa mãn điều kiện khoảng cách và thời gian.
        Trả về LinkedEvent với cặp phù hợp nhất (gần nhất).
        """
        persons = self._person_buf.get(vehicle.source_id, [])
        if not persons:
            return None

        vcx, vcy = self._center(vehicle.bbox)
        best_person = None
        best_dist   = float("inf")

        for person in persons:
            # Kiểm tra cửa sổ thời gian
            if abs(vehicle.timestamp - person.timestamp) > self._temporal:
                continue

            dist = self._euclidean(vcx, vcy, *self._center(person.bbox))
            if dist <= self._max_dist and dist < best_dist:
                best_dist   = dist
                best_person = person

        if best_person is None:
            return None

        return self._create_link(vehicle, best_person, best_dist)

    def _try_link_person(self, person: PersonObservation) -> Optional[LinkedEvent]:
        """
        Với một người vừa thêm vào, tìm xe đang chờ trong cùng source_id.
        """
        vehicles = self._vehicle_buf.get(person.source_id, [])
        if not vehicles:
            return None

        pcx, pcy = self._center(person.bbox)
        best_vehicle = None
        best_dist    = float("inf")

        for vehicle in vehicles:
            if abs(person.timestamp - vehicle.timestamp) > self._temporal:
                continue
            dist = self._euclidean(pcx, pcy, *self._center(vehicle.bbox))
            if dist <= self._max_dist and dist < best_dist:
                best_dist    = dist
                best_vehicle = vehicle

        if best_vehicle is None:
            return None

        return self._create_link(best_vehicle, person, best_dist)

    def _create_link(
        self,
        vehicle: VehicleObservation,
        person:  PersonObservation,
        dist:    float,
    ) -> Optional[LinkedEvent]:
        """
        Tạo LinkedEvent từ cặp xe-người đã tìm được.
        Kiểm tra dedup: nếu cặp này đã được ghép gần đây → bỏ qua.
        Xóa cả 2 observation khỏi buffer sau khi ghép thành công.
        """
        dedup_key = (vehicle.plate_number, person.person_id)
        now = time.monotonic()
        last_linked = self._linked_pairs.get(dedup_key, 0.0)
        if now - last_linked < self._dedup_secs:
            logger.debug(
                "ObjectLinker dedup: plate=%s person=%s — bỏ qua ghép lại.",
                vehicle.plate_number, person.person_id,
            )
            return None

        # Đánh dấu đã ghép
        self._linked_pairs[dedup_key] = now

        # Xóa khỏi buffer
        v_buf = self._vehicle_buf.get(vehicle.source_id, [])
        p_buf = self._person_buf.get(person.source_id, [])
        if vehicle in v_buf:
            v_buf.remove(vehicle)
        if person in p_buf:
            p_buf.remove(person)

        linked = LinkedEvent(
            vehicle     = vehicle,
            person      = person,
            distance_px = round(dist, 2),
            source_id   = vehicle.source_id,
            camera_id   = vehicle.camera_id,
            linked_at   = datetime.now(_VN_TZ).isoformat(),
            metadata    = {
                "plate_number":   vehicle.plate_number,
                "plate_category": vehicle.plate_category,
                "person_id":      person.person_id,
                "person_name":    person.person_name,
                "person_role":    person.person_role,
                "fr_confidence":  person.fr_confidence,
                "is_stranger":    person.is_stranger,
                "distance_px":    round(dist, 2),
            },
        )
        logger.info(
            "🔗 Object Linked! plate=%s person=%s dist=%.1fpx src=%s",
            vehicle.plate_number, person.person_id, dist, vehicle.source_id,
        )
        return linked

    # ──────────────────────────────────────────────────────────────────────────
    # Dọn dẹp buffer
    # ──────────────────────────────────────────────────────────────────────────

    def _flush_expired(self, source_id: str, now: float) -> None:
        """
        Xóa các observation cũ hơn _BUFFER_MAX_AGE giây khỏi buffer.
        Gọi tự động trước mỗi lần add để ngăn tích lũy không giới hạn.
        """
        cutoff = now - _BUFFER_MAX_AGE

        v_buf = self._vehicle_buf.get(source_id, [])
        self._vehicle_buf[source_id] = [v for v in v_buf if v.timestamp > cutoff]

        p_buf = self._person_buf.get(source_id, [])
        self._person_buf[source_id]  = [p for p in p_buf if p.timestamp > cutoff]

        # Dọn dedup map định kỳ
        if len(self._linked_pairs) > 2000:
            cutoff_dedup = now - self._dedup_secs * 2
            self._linked_pairs = {k: v for k, v in self._linked_pairs.items() if v > cutoff_dedup}

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers hình học
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _center(bbox: tuple[int, int, int, int]) -> tuple[float, float]:
        """Tính tọa độ tâm của bounding box (x1, y1, x2, y2)."""
        x1, y1, x2, y2 = bbox
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    @staticmethod
    def _euclidean(x1: float, y1: float, x2: float, y2: float) -> float:
        """Tính khoảng cách Euclidean giữa 2 điểm."""
        return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5


# ── Singleton instance ────────────────────────────────────────────────────────
object_linker = ObjectLinker()
