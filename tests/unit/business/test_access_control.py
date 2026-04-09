"""
Unit tests for AccessController in src/business/access_control.py

Tests: can_open() conditions, trigger() with mocked HTTP relay, rate limiting.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import time
import pytest
from unittest.mock import patch, MagicMock
from urllib.error import URLError

from src.business.access_control import AccessController, DoorEvent, DoorConfig


# ─── Helpers ─────────────────────────────────────────────────────────────────

EXAMPLE_DOOR = {
    "door_id": "door_01",
    "name": "Main Entrance",
    "relay_url": "http://192.168.1.10/relay/1",
    "zone": "lobby",
    "open_ms": 5000,
    "enabled": True,
}


def make_controller(doors=None):
    ctrl = AccessController()
    ctrl.initialize(doors or [EXAMPLE_DOOR])
    return ctrl


# ─── can_open() ──────────────────────────────────────────────────────────────

class TestCanOpen:

    def test_not_initialized_returns_false(self):
        ctrl = AccessController()
        ok, reason = ctrl.can_open("P001", "staff", "door_01")
        assert ok is False
        assert "khởi tạo" in reason.lower() or "initialized" in reason.lower()

    def test_nonexistent_door_returns_false(self):
        ctrl = make_controller()
        ok, reason = ctrl.can_open("P001", "staff", "door_NONE")
        assert ok is False
        assert "door_NONE" in reason

    def test_disabled_door_returns_false(self):
        ctrl = make_controller([{**EXAMPLE_DOOR, "enabled": False}])
        ok, reason = ctrl.can_open("P001", "staff", "door_01")
        assert ok is False
        assert "vô hiệu hóa" in reason.lower() or "disabled" in reason.lower()

    def test_liveness_fail_blocks_access(self):
        ctrl = make_controller()
        ok, reason = ctrl.can_open("P001", "staff", "door_01", liveness_ok=False)
        assert ok is False
        assert "giả mạo" in reason.lower() or "spoof" in reason.lower()

    def test_zone_denied_blocks_access(self):
        ctrl = make_controller()
        ok, reason = ctrl.can_open("P001", "visitor", "door_01", zone_allowed=False)
        assert ok is False

    def test_rate_limit_blocks_within_cooldown(self):
        ctrl = make_controller()
        ctrl._open_ts["P001"] = time.monotonic()  # Simulate recent open
        ok, reason = ctrl.can_open("P001", "staff", "door_01")
        assert ok is False
        assert "rate" in reason.lower() or "chờ" in reason.lower()

    def test_valid_conditions_returns_true(self):
        ctrl = make_controller()
        ok, reason = ctrl.can_open("P001", "staff", "door_01",
                                   liveness_ok=True, zone_allowed=True)
        assert ok is True
        assert reason == "OK"


# ─── trigger() ───────────────────────────────────────────────────────────────

class TestTrigger:

    def _mock_relay_success(self):
        """Patch urllib.request.urlopen to return success."""
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200
        return mock_resp

    def test_trigger_granted_when_relay_success(self):
        ctrl = make_controller()
        with patch("urllib.request.urlopen", return_value=self._mock_relay_success()):
            event = ctrl.trigger(
                door_id="door_01",
                person_id="P001",
                person_name="Alice",
                person_role="staff",
                camera_id="cam_01",
                source_id="cam_01",
                liveness_ok=True,
                zone_allowed=True,
            )
        assert isinstance(event, DoorEvent)
        assert event.granted is True
        assert event.door_id == "door_01"
        assert event.person_id == "P001"

    def test_trigger_denied_when_relay_fails(self):
        ctrl = make_controller()
        with patch("urllib.request.urlopen", side_effect=URLError("connection refused")):
            event = ctrl.trigger(
                door_id="door_01",
                person_id="P002",
                person_name="Bob",
                person_role="staff",
                camera_id="cam_01",
                source_id="cam_01",
                liveness_ok=True,
                zone_allowed=True,
            )
        assert isinstance(event, DoorEvent)
        assert event.granted is False
        assert "relay" in event.reason.lower() or "lỗi" in event.reason.lower()

    def test_trigger_denied_when_conditions_fail(self):
        ctrl = make_controller()
        event = ctrl.trigger(
            door_id="door_01",
            person_id="P003",
            person_name="Charlie",
            person_role="staff",
            camera_id="cam_01",
            source_id="cam_01",
            liveness_ok=False,  # Spoof detected
            zone_allowed=True,
        )
        assert event.granted is False

    def test_trigger_updates_rate_limit(self):
        ctrl = make_controller()
        with patch("urllib.request.urlopen", return_value=self._mock_relay_success()):
            ctrl.trigger("door_01", "P001", "Alice", "staff", "cam_01", "cam_01",
                         liveness_ok=True, zone_allowed=True)
        # After granted, rate limit should be set
        assert "P001" in ctrl._open_ts
        # Second trigger within cooldown → denied without relay call
        event = ctrl.trigger("door_01", "P001", "Alice", "staff", "cam_01", "cam_01",
                              liveness_ok=True, zone_allowed=True)
        assert event.granted is False

    def test_trigger_contains_expected_fields(self):
        ctrl = make_controller()
        with patch("urllib.request.urlopen", return_value=self._mock_relay_success()):
            event = ctrl.trigger("door_01", "P001", "Alice", "staff", "cam_01", "cam_01")
        assert event.door_id == "door_01"
        assert event.person_id == "P001"
        assert event.timestamp  # non-empty


# ─── Utility methods ─────────────────────────────────────────────────────────

class TestAccessControllerUtils:

    def test_get_door_existing(self):
        ctrl = make_controller()
        door = ctrl.get_door("door_01")
        assert isinstance(door, DoorConfig)
        assert door.door_id == "door_01"

    def test_get_door_nonexistent(self):
        ctrl = make_controller()
        assert ctrl.get_door("nonexistent") is None

    def test_list_doors(self):
        ctrl = make_controller()
        doors = ctrl.list_doors()
        assert len(doors) == 1
        assert doors[0].door_id == "door_01"

    def test_set_door_enabled_and_disabled(self):
        ctrl = make_controller()
        ok = ctrl.set_door_enabled("door_01", False)
        assert ok is True
        assert ctrl.get_door("door_01").enabled is False

        ok = ctrl.set_door_enabled("door_01", True)
        assert ok is True
        assert ctrl.get_door("door_01").enabled is True

    def test_set_door_nonexistent_returns_false(self):
        ctrl = make_controller()
        assert ctrl.set_door_enabled("ghost_door", True) is False
