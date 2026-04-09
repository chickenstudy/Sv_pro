"""
Unit tests for Pipeline Watchdog & Circuit Breaker
(src/watchdog/pipeline_watchdog.py).
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import pytest
from unittest.mock import MagicMock, patch

from src.watchdog.pipeline_watchdog import (
    CircuitBreaker,
    ComponentHealth,
    PipelineWatchdog,
    SERVICE_JSON_EGRESS,
    SERVICE_AI_CORE,
    SERVICE_VIDEO_INGRESS,
)


# ── ComponentHealth ───────────────────────────────────────────────────────────

class TestComponentHealth:

    def test_record_restart_increments_count(self):
        h = ComponentHealth("test-service")
        h.record_restart()
        h.record_restart()
        assert h.restart_count == 2

    def test_recent_restarts_within_window(self):
        h = ComponentHealth("test-service")
        h.record_restart()
        h.record_restart()
        assert h.recent_restarts(window_secs=600) == 2

    def test_recent_restarts_old_entries_excluded(self):
        h = ComponentHealth("test-service")
        # Manually add an old restart timestamp
        h._restart_history.append(time.monotonic() - 700)  # 700s ago
        h.record_restart()  # recent
        assert h.recent_restarts(window_secs=600) == 1  # Only recent

    def test_initial_state(self):
        h = ComponentHealth("json-egress")
        assert h.is_healthy is True
        assert h.restart_count == 0
        assert h.circuit_opens == 0


# ── CircuitBreaker ─────────────────────────────────────────────────────────

class TestCircuitBreaker:

    def test_can_restart_when_below_limit(self):
        h = ComponentHealth("svc")
        cb = CircuitBreaker(h, max_restarts=3)
        assert cb.can_restart() is True

    def test_circuit_opens_at_limit(self):
        h = ComponentHealth("svc")
        cb = CircuitBreaker(h, max_restarts=3)
        for _ in range(3):
            h.record_restart()
        # Now at limit → circuit should open
        assert cb.can_restart() is False
        assert cb.is_open is True

    def test_circuit_open_increments_counter(self):
        h = ComponentHealth("svc")
        cb = CircuitBreaker(h, max_restarts=2)
        for _ in range(2):
            h.record_restart()
        cb.can_restart()  # triggers open
        assert h.circuit_opens == 1

    def test_force_close_allows_restart(self):
        h = ComponentHealth("svc")
        cb = CircuitBreaker(h, max_restarts=2)
        for _ in range(2):
            h.record_restart()
        cb.can_restart()  # Opens circuit
        assert cb.is_open is True
        cb.force_close()
        assert cb.is_open is False
        assert cb.can_restart() is True

    def test_circuit_stays_closed_below_limit(self):
        h = ComponentHealth("svc")
        cb = CircuitBreaker(h, max_restarts=5)
        h.record_restart()
        h.record_restart()
        assert cb.can_restart() is True
        assert cb.is_open is False


# ── PipelineWatchdog ──────────────────────────────────────────────────────────

class TestPipelineWatchdog:

    def _make_watchdog(self, on_restart=None, **kwargs):
        """Make watchdog with custom restart callback (no docker)."""
        defaults = dict(
            check_interval_secs=0.1,
            stuck_threshold_secs=0.5,
        )
        defaults.update(kwargs)  # kwargs override defaults
        return PipelineWatchdog(
            on_restart_cb=on_restart or MagicMock(return_value=True),
            **defaults,
        )

    def test_initial_status_ok(self):
        w = self._make_watchdog()
        status = w.get_status()
        assert status["running"] is False
        assert "pipeline_stuck" in status
        assert "components" in status

    def test_notify_json_activity_resets_stuck(self):
        w = self._make_watchdog()
        w.notify_json_activity("cam_01", count=5)
        assert w._egress_events == 5
        status = w.get_status()
        assert status["last_json_age_s"] < 1.0

    def test_pipeline_stuck_after_threshold(self):
        w = self._make_watchdog(stuck_threshold_secs=0.01)
        time.sleep(0.05)  # Wait past threshold
        status = w.get_status()
        assert status["pipeline_stuck"] is True

    def test_force_restart_calls_callback(self):
        restart_cb = MagicMock(return_value=True)
        w = self._make_watchdog(on_restart=restart_cb)
        w.force_restart(SERVICE_JSON_EGRESS)
        restart_cb.assert_called_once_with(SERVICE_JSON_EGRESS)

    def test_circuit_breaker_blocks_after_limit(self):
        restart_cb = MagicMock(return_value=True)
        w = self._make_watchdog(on_restart=restart_cb, max_restarts_per_window=2)
        # Force 2 restarts → hits limit
        w.force_restart(SERVICE_AI_CORE)
        w.force_restart(SERVICE_AI_CORE)
        restart_cb.reset_mock()
        # 3rd restart should be blocked by circuit breaker
        result = w._do_restart(SERVICE_AI_CORE, force=False)
        assert result is False
        restart_cb.assert_not_called()

    def test_force_restart_bypasses_circuit(self):
        restart_cb = MagicMock(return_value=True)
        w = self._make_watchdog(on_restart=restart_cb, max_restarts_per_window=1)
        w.force_restart(SERVICE_AI_CORE)  # hits limit
        restart_cb.reset_mock()
        # Force bypass circuit
        result = w.force_restart(SERVICE_AI_CORE)
        assert result is True
        restart_cb.assert_called()

    def test_watchdog_start_stop(self):
        restart_cb = MagicMock()
        w = self._make_watchdog(on_restart=restart_cb)
        w.start()
        assert w._running is True
        w.stop()
        assert w._running is False

    def test_force_close_circuit_allows_restart(self):
        restart_cb = MagicMock(return_value=True)
        w = self._make_watchdog(on_restart=restart_cb, max_restarts_per_window=1)
        w.force_restart(SERVICE_JSON_EGRESS)
        # Circuit should be open now
        result = w._do_restart(SERVICE_JSON_EGRESS, force=False)
        assert result is False
        # Force close
        w.force_close_circuit(SERVICE_JSON_EGRESS)
        restart_cb.reset_mock()
        result = w._do_restart(SERVICE_JSON_EGRESS, force=False)
        assert result is True
