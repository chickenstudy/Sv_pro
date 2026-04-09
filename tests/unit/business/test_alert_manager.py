"""
Unit tests for AlertManager in src/business/alert_manager.py

Tests: rate limiting, queue, dispatch logic (without sending real HTTP requests).
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import time
import pytest
from unittest.mock import patch, MagicMock

from src.business.alert_manager import AlertManager, _DEFAULT_RATE_SECS
from src.business.blacklist_engine import BlacklistEvent, Severity


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_event(entity_id="P001", event_type="blacklist_person"):
    return BlacklistEvent(
        event_type=event_type,
        entity_type="person",
        entity_id=entity_id,
        entity_name="Test Person",
        severity=Severity.HIGH,
        camera_id="cam_01",
        source_id="cam_01",
        reason="Test reason",
        timestamp="2026-01-01T00:00:00+07:00",
    )


# ─── Initialization ───────────────────────────────────────────────────────────

class TestAlertManagerInit:

    def test_not_initialized_rejects_alert(self):
        """send_alert() before initialize() → returns False."""
        mgr = AlertManager()
        result = mgr.send_alert(make_event())
        assert result is False

    def test_initialized_flag_set(self):
        mgr = AlertManager()
        mgr.initialize()
        assert mgr._initialized is True


# ─── Rate Limiting ────────────────────────────────────────────────────────────

class TestAlertRateLimiting:

    def test_first_alert_passes(self):
        """First alert for an entity → enqueued successfully."""
        mgr = AlertManager()
        mgr.initialize(rate_secs=300)
        result = mgr.send_alert(make_event("P001"))
        assert result is True

    def test_second_alert_same_entity_throttled(self):
        """Second alert within rate_secs → blocked."""
        mgr = AlertManager()
        mgr.initialize(rate_secs=300)
        mgr.send_alert(make_event("P002"))
        # Immediately send again → should be throttled
        result = mgr.send_alert(make_event("P002"))
        assert result is False

    def test_different_entities_not_throttled(self):
        """Different entity IDs → each gets through."""
        mgr = AlertManager()
        mgr.initialize(rate_secs=300)
        r1 = mgr.send_alert(make_event("P_A"))
        r2 = mgr.send_alert(make_event("P_B"))
        assert r1 is True
        assert r2 is True

    def test_rate_window_resets_after_secs(self):
        """After rate_secs passes → same entity allowed again."""
        mgr = AlertManager()
        mgr.initialize(rate_secs=0.05)  # 50ms window
        mgr.send_alert(make_event("P003"))
        time.sleep(0.1)  # wait for window to pass
        result = mgr.send_alert(make_event("P003"))
        assert result is True

    def test_global_rpm_limit(self):
        """After global_rpm alerts in one minute → subsequent ones blocked."""
        mgr = AlertManager()
        mgr.initialize(rate_secs=0.0, global_rpm=3)  # allow 3/minute
        mgr._minute_start = time.monotonic()
        # Send 3 alerts for different entities
        for i in range(3):
            mgr.send_alert(make_event(f"ENTITY_{i}"))
        # 4th alert in same minute window → blocked
        result = mgr.send_alert(make_event("ENTITY_EXTRA"))
        assert result is False

    def test_queue_full_returns_false(self):
        """When queue is full → send_alert returns False."""
        mgr = AlertManager()
        mgr.initialize(rate_secs=0.0, global_rpm=9999)
        # Fill the queue manually
        mgr._queue.maxsize = 2
        # Bypass rate limit tracking for this test
        mgr._queue.put_nowait(("dummy", None))
        mgr._queue.put_nowait(("dummy", None))
        result = mgr.send_alert(make_event("QUEUE_FULL"))
        assert result is False


# ─── Telegram text builder ────────────────────────────────────────────────────

class TestTelegramTextBuilder:

    def _make_mgr(self):
        mgr = AlertManager()
        mgr.initialize()
        return mgr

    def test_blacklist_person_text(self):
        mgr = self._make_mgr()
        event = make_event(event_type="blacklist_person")
        text = mgr._build_telegram_text(event)
        assert "Camera" in text
        assert "cam_01" in text
        assert "P001" in text or "Test Person" in text

    def test_blacklist_vehicle_text(self):
        mgr = self._make_mgr()
        event = BlacklistEvent(
            event_type="blacklist_vehicle",
            entity_type="vehicle",
            entity_id="51A-12345",
            entity_name="51A-12345",
            severity=Severity.HIGH,
            camera_id="cam_01",
            source_id="cam_01",
            reason="Stolen",
            timestamp="2026-01-01T00:00:00+07:00",
        )
        text = mgr._build_telegram_text(event)
        assert "51A-12345" in text
        assert "Biển số" in text or "xe" in text.lower() or "Stolen" in text

    def test_spoof_detected_text(self):
        mgr = self._make_mgr()
        event = make_event(event_type="spoof_detected")
        text = mgr._build_telegram_text(event)
        assert "giả mạo" in text or "Spoof" in text or "spoof" in text.lower()

    def test_severity_icons_present(self):
        mgr = self._make_mgr()
        for severity, expected_icon in [
            (Severity.HIGH, "🔴"),
            (Severity.MEDIUM, "🟠"),
            (Severity.CRITICAL, "💀"),
        ]:
            event = make_event()
            event.severity = severity
            text = mgr._build_telegram_text(event)
            assert expected_icon in text, f"Expected {expected_icon} for {severity}"
