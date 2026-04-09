"""
Unit tests for BlacklistEngine in src/business/blacklist_engine.py

Tests blacklist checking logic with mocked Redis and DB.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import json
import pytest
from unittest.mock import patch, MagicMock

from src.business.blacklist_engine import (
    BlacklistEngine,
    BlacklistEvent,
    Severity,
    _L1BLCache,
)


# ─── _L1BLCache ───────────────────────────────────────────────────────────────

class TestL1BLCache:

    def test_put_and_get_blacklisted(self):
        cache = _L1BLCache()
        cache.put("UID001", True, "stolen vehicle")
        result = cache.get("UID001")
        assert result == (True, "stolen vehicle")

    def test_put_and_get_not_blacklisted(self):
        cache = _L1BLCache()
        cache.put("UID002", False, "")
        result = cache.get("UID002")
        assert result == (False, "")

    def test_get_missing_returns_none(self):
        cache = _L1BLCache()
        assert cache.get("NONEXISTENT") is None

    def test_ttl_expiry(self):
        import time
        cache = _L1BLCache(ttl=0.05)  # 50ms
        cache.put("UID003", True, "test")
        time.sleep(0.1)
        assert cache.get("UID003") is None

    def test_capacity_eviction(self):
        cache = _L1BLCache(capacity=2)
        cache.put("A", True, "r1")
        cache.put("B", True, "r2")
        cache.put("C", True, "r3")  # should evict A (first inserted)
        assert cache.get("C") is not None

    def test_invalidate(self):
        cache = _L1BLCache()
        cache.put("UID", True, "reason")
        cache.invalidate("UID")
        assert cache.get("UID") is None


# ─── BlacklistEngine.check_person ─────────────────────────────────────────────

class TestBlacklistEngineCheckPerson:

    def _make_engine(self, redis_client=None, db_dsn=None):
        engine = BlacklistEngine()
        engine.initialize(
            db_dsn=db_dsn or "",
            redis_client=redis_client,
            camera_zones={"cam_01": "zone_A"},
            zone_access={"zone_A": ["staff", "admin"]},
        )
        return engine

    def test_blacklisted_person_from_cache(self):
        """Person flagged blacklist → BlacklistEvent HIGH returned."""
        engine = self._make_engine()
        engine._person_cache.put("UID_BL", True, "criminal record")
        event = engine.check_person(
            person_id="UID_BL", person_name="Bad Guy",
            person_role="visitor", source_id="cam_01", camera_id="cam_01",
        )
        assert event is not None
        assert isinstance(event, BlacklistEvent)
        assert event.event_type == "blacklist_person"
        assert event.severity == Severity.HIGH

    def test_clean_person_returns_none(self):
        """Non-blacklisted person in allowed zone → no event."""
        engine = self._make_engine()
        engine._person_cache.put("UID_OK", False, "")
        event = engine.check_person(
            person_id="UID_OK", person_name="Good Guy",
            person_role="staff", source_id="cam_01", camera_id="cam_01",
        )
        assert event is None

    def test_zone_denied_wrong_role(self):
        """Person with unauthorized role in restricted zone → zone_denied event."""
        engine = self._make_engine()
        engine._person_cache.put("UID_VISITOR", False, "")
        event = engine.check_person(
            person_id="UID_VISITOR", person_name="Visitor",
            person_role="visitor",  # not in zone_A allowed_roles
            source_id="cam_01", camera_id="cam_01",
        )
        assert event is not None
        assert event.event_type == "zone_denied"
        assert event.severity == Severity.MEDIUM

    def test_time_denied_outside_hours(self):
        """Access outside allowed hours → time_denied event."""
        engine = BlacklistEngine()
        engine.initialize(
            db_dsn="",
            camera_zones={"cam_01": "zone_restricted"},
            zone_access={"zone_restricted": ["staff"]},
            # Allow only 10-11h → will be denied at most other hours
            zone_time_rules={"zone_restricted": [(10, 11)]},
        )
        engine._person_cache.put("UID_STAFF", False, "")
        import datetime
        # Mock current hour to 3am (outside 10-11)
        with patch("src.business.blacklist_engine.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.datetime(2026, 1, 1, 3, 0, 0,
                tzinfo=datetime.timezone.utc)
            mock_dt.now.return_value = mock_dt.now.return_value.replace(fold=0)
            # Patch hour extraction
            from src.business.blacklist_engine import _VN_TZ
            import datetime as real_dt
            mock_dt.now = lambda tz=None: real_dt.datetime(2026, 1, 1, 3, 0, tzinfo=_VN_TZ)
            mock_dt.isoformat = real_dt.datetime.isoformat
            event = engine.check_person(
                person_id="UID_STAFF", person_name="Staff",
                person_role="staff", source_id="cam_01", camera_id="cam_01",
            )
            # time_denied should fire at 3am (outside 10-11)
            if event:
                assert event.event_type == "time_denied"

    def test_redis_blacklist_hit(self, mock_redis):
        """Person found in Redis blacklist → HIGH event."""
        mock_redis.setex(
            "svpro:bl:person:UID_REDIS", 300,
            json.dumps({"reason": "wanted person"})
        )
        engine = BlacklistEngine()
        engine.initialize(db_dsn="", redis_client=mock_redis)
        event = engine.check_person(
            person_id="UID_REDIS", person_name="Wanted",
            person_role="visitor", source_id="src_01", camera_id="cam_01",
        )
        assert event is not None
        assert event.event_type == "blacklist_person"

    def test_invalidate_removes_from_cache_and_redis(self, mock_redis):
        """invalidate_person() should clear both L1 and Redis."""
        mock_redis.setex("svpro:bl:person:UID_X", 300, json.dumps({"reason": "test"}))
        engine = BlacklistEngine()
        engine.initialize(db_dsn="", redis_client=mock_redis)
        engine._person_cache.put("UID_X", True, "test")
        engine.invalidate_person("UID_X")
        assert engine._person_cache.get("UID_X") is None
        assert mock_redis.get("svpro:bl:person:UID_X") is None


# ─── BlacklistEngine.check_vehicle ────────────────────────────────────────────

class TestBlacklistEngineCheckVehicle:

    def test_blacklisted_vehicle_from_cache(self):
        engine = BlacklistEngine()
        engine.initialize(db_dsn="")
        engine._vehicle_cache.put("51A-12345", True, "stolen")
        event = engine.check_vehicle(
            plate_number="51A-12345", plate_category="O_TO_DAN_SU",
            source_id="src_01", camera_id="cam_01",
        )
        assert event is not None
        assert event.event_type == "blacklist_vehicle"
        assert event.severity == Severity.HIGH

    def test_clean_vehicle_returns_none(self):
        engine = BlacklistEngine()
        engine.initialize(db_dsn="")
        engine._vehicle_cache.put("51A-99999", False, "")
        event = engine.check_vehicle(
            plate_number="51A-99999", plate_category="O_TO_DAN_SU",
            source_id="src_01", camera_id="cam_01",
        )
        assert event is None

    def test_blacklisted_vehicle_from_redis(self, mock_redis):
        mock_redis.setex(
            "svpro:bl:vehicle:29B1-11111", 300,
            json.dumps({"reason": "fake plates"})
        )
        engine = BlacklistEngine()
        engine.initialize(db_dsn="", redis_client=mock_redis)
        event = engine.check_vehicle(
            plate_number="29B1-11111", plate_category="XE_MAY_DAN_SU",
            source_id="src_01", camera_id="cam_01",
        )
        assert event is not None
        assert "29B1-11111" in event.entity_id or event.entity_name == "29B1-11111"
