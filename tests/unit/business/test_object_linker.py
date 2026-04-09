"""
Unit tests for ObjectLinker in src/business/object_linker.py

Tests spatial + temporal linking of vehicle (LPR) and person (FR) observations.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import time
import pytest

from src.business.object_linker import (
    ObjectLinker,
    VehicleObservation,
    PersonObservation,
    LinkedEvent,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_vehicle(plate="51A-12345", bbox=(100, 100, 300, 300),
                 source_id="cam_01", ts=None):
    return VehicleObservation(
        plate_number=plate,
        plate_category="O_TO_DAN_SU",
        bbox=bbox,
        source_id=source_id,
        camera_id=source_id,
        timestamp=ts or time.monotonic(),
    )


def make_person(person_id="P001", bbox=(150, 150, 250, 350),
                source_id="cam_01", ts=None):
    return PersonObservation(
        person_id=person_id,
        person_name="TestPerson",
        person_role="staff",
        fr_confidence=0.85,
        bbox=bbox,
        source_id=source_id,
        camera_id=source_id,
        timestamp=ts or time.monotonic(),
    )


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestObjectLinker:

    def test_close_objects_same_time_link(self):
        """Vehicle and person within 150px distance + same time → LinkedEvent."""
        linker = ObjectLinker(max_pixel_dist=150, temporal_secs=2.0)
        now = time.monotonic()
        v = make_vehicle(bbox=(100, 100, 300, 300), ts=now)
        p = make_person(bbox=(200, 100, 300, 300), ts=now)  # center close to vehicle

        linker.add_vehicle(v)
        event = linker.add_person(p)
        assert event is not None
        assert isinstance(event, LinkedEvent)
        assert event.vehicle.plate_number == "51A-12345"
        assert event.person.person_id == "P001"

    def test_far_objects_not_linked(self):
        """Vehicle and person more than max_pixel_dist apart → None."""
        linker = ObjectLinker(max_pixel_dist=50, temporal_secs=2.0)
        now = time.monotonic()
        # Vehicle center ≈ (200, 200); Person center ≈ (600, 600) → distance ≈ 566px
        v = make_vehicle(bbox=(100, 100, 300, 300), ts=now)
        p = make_person(bbox=(500, 500, 700, 700), ts=now)

        linker.add_vehicle(v)
        event = linker.add_person(p)
        assert event is None

    def test_different_source_not_linked(self):
        """Vehicle on cam_01 and person on cam_02 → not linked."""
        linker = ObjectLinker(max_pixel_dist=500, temporal_secs=5.0)
        now = time.monotonic()
        v = make_vehicle(source_id="cam_01", ts=now)
        p = make_person(source_id="cam_02", ts=now)

        linker.add_vehicle(v)
        event = linker.add_person(p)
        assert event is None

    def test_temporal_gap_prevents_linking(self):
        """If time gap > temporal_secs → no link."""
        linker = ObjectLinker(max_pixel_dist=200, temporal_secs=1.0)
        now = time.monotonic()
        v = make_vehicle(ts=now - 5.0)   # 5 seconds ago
        p = make_person(ts=now)           # now

        linker.add_vehicle(v)
        event = linker.add_person(p)
        assert event is None

    def test_dedup_prevents_relinking_same_pair(self):
        """Same plate + person pair should not be linked again within dedup_secs."""
        linker = ObjectLinker(max_pixel_dist=200, temporal_secs=2.0)
        linker._dedup_secs = 30.0
        now = time.monotonic()

        v1 = make_vehicle(ts=now)
        p1 = make_person(ts=now)
        linker.add_vehicle(v1)
        event1 = linker.add_person(p1)
        assert event1 is not None  # First link succeeds

        # Try to link the same pair again immediately
        v2 = make_vehicle(ts=now + 0.1)
        p2 = make_person(ts=now + 0.1)
        linker.add_vehicle(v2)
        event2 = linker.add_person(p2)
        assert event2 is None  # Dedup blocks second link

    def test_linked_event_contains_metadata(self):
        """LinkedEvent should contain expected metadata fields."""
        linker = ObjectLinker(max_pixel_dist=200, temporal_secs=2.0)
        now = time.monotonic()
        linker.add_vehicle(make_vehicle(ts=now))
        event = linker.add_person(make_person(ts=now))
        assert event is not None
        assert "plate_number" in event.metadata
        assert "person_id" in event.metadata
        assert "distance_px" in event.metadata
        assert event.distance_px >= 0

    def test_flush_expired_removes_old_observations(self):
        """Old observations (> max age) should be removed from buffers."""
        linker = ObjectLinker(max_pixel_dist=200, temporal_secs=2.0)
        from src.business.object_linker import _BUFFER_MAX_AGE
        now = time.monotonic()
        # Add vehicle with timestamp in the past (beyond max age)
        old_v = make_vehicle(ts=now - _BUFFER_MAX_AGE - 1.0)
        linker._vehicle_buf.setdefault("cam_01", []).append(old_v)

        # Adding new person triggers flush
        new_p = make_person(ts=now)
        linker.add_vehicle(make_vehicle(ts=now))
        event = linker.add_person(new_p)

        # Old vehicle should have been removed, only new vehicle eligible
        # The new vehicle should match the new person
        assert event is not None

    def test_center_calculation(self):
        """_center() should return correct centroid."""
        cx, cy = ObjectLinker._center((100, 200, 300, 400))
        assert cx == pytest.approx(200.0)
        assert cy == pytest.approx(300.0)

    def test_euclidean_distance(self):
        """_euclidean() should return correct distance."""
        dist = ObjectLinker._euclidean(0, 0, 3, 4)
        assert dist == pytest.approx(5.0)
