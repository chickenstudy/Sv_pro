"""
Unit tests for EOS Storm Guard (src/ingress/eos_guard.py).
Tests: rate detection, cooldown, multi-camera registry, callbacks.
"""

import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import pytest
from src.ingress.eos_guard import EosStormGuard, EosGuardRegistry, _COOLDOWN_SECS


class TestEosStormGuard:

    def test_single_eos_allowed(self):
        """Một EOS duy nhất phải được forward."""
        guard = EosStormGuard(source_id="cam_01", threshold=5)
        assert guard.should_forward() is True

    def test_below_threshold_allowed(self):
        """5 EOS trong 1 giây với threshold=5 → không trigger storm."""
        guard = EosStormGuard(source_id="cam_02", threshold=5, window_secs=2.0)
        for _ in range(5):
            assert guard.should_forward() is True

    def test_above_threshold_triggers_storm(self):
        """6 EOS trong cửa sổ → thứ 7 phải bị block."""
        guard = EosStormGuard(source_id="cam_03", threshold=5, window_secs=10.0)
        for _ in range(6):
            guard.should_forward()
        # 7th call should return False (storm active)
        result = guard.should_forward()
        assert result is False

    def test_storm_active_flag(self):
        """is_storm_active phải là True sau khi storm kích hoạt."""
        guard = EosStormGuard(source_id="cam_04", threshold=3, window_secs=10.0)
        for _ in range(4):  # > threshold
            guard.should_forward()
        assert guard.is_storm_active is True

    def test_on_storm_callback_called(self):
        """on_storm callback phải được gọi khi storm kích hoạt."""
        called = threading.Event()
        def callback(source_id):
            assert source_id == "cam_05"
            called.set()

        guard = EosStormGuard(source_id="cam_05", threshold=2, on_storm=callback)
        for _ in range(3):
            guard.should_forward()
        # Callback runs in daemon thread — wait briefly
        called.wait(timeout=1.0)
        assert called.is_set(), "on_storm callback was not called"

    def test_storm_drops_are_blocked(self):
        """Tất cả EOS trong cooldown phải bị block (return False)."""
        guard = EosStormGuard(source_id="cam_06", threshold=2, window_secs=10.0)
        for _ in range(3):
            guard.should_forward()
        # Now in storm — all subsequent should be blocked
        for _ in range(5):
            assert guard.should_forward() is False

    def test_manual_reset_clears_storm(self):
        """guard.reset() phải xóa trạng thái storm."""
        guard = EosStormGuard(source_id="cam_07", threshold=2, window_secs=10.0)
        for _ in range(3):
            guard.should_forward()
        assert guard.is_storm_active is True
        guard.reset()
        assert guard.is_storm_active is False
        # After reset, EOS should be forwarded again
        assert guard.should_forward() is True

    def test_eos_rate_property(self):
        """eos_rate phải trả về số EOS/giây ước tính dương."""
        guard = EosStormGuard(source_id="cam_08", threshold=100, window_secs=2.0)
        for _ in range(5):
            guard.should_forward()
        assert guard.eos_rate > 0

    def test_window_reset_allows_new_eos(self):
        """Sau khi window reset, EOS counter nên reset về 0."""
        guard = EosStormGuard(source_id="cam_09", threshold=3, window_secs=0.05)
        for _ in range(3):
            guard.should_forward()
        assert guard.is_storm_active is False  # Didn't exceed threshold (==)
        # Wait for window to expire
        time.sleep(0.1)
        # Counter should reset — 1 EOS should be fine
        assert guard.should_forward() is True
        assert guard.is_storm_active is False


class TestEosGuardRegistry:

    def test_registry_creates_guard_per_source(self):
        """Registry phải tạo một guard khác nhau cho mỗi source_id."""
        reg = EosGuardRegistry(threshold=10)
        g1 = reg.get("src_A")
        g2 = reg.get("src_B")
        assert g1 is not g2

    def test_registry_returns_same_guard(self):
        """Cùng source_id phải trả về cùng guard object."""
        reg = EosGuardRegistry(threshold=10)
        g1 = reg.get("src_X")
        g2 = reg.get("src_X")
        assert g1 is g2

    def test_shortcut_should_forward(self):
        """Registry.should_forward() phải delegate đến guard của source."""
        reg = EosGuardRegistry(threshold=10, window_secs=5.0)
        # 10 EOS below threshold → all forwarded
        for _ in range(10):
            assert reg.should_forward("cam_reg_01") is True

    def test_active_storms_list(self):
        """active_storms() phải trả về list source_id đang có storm."""
        reg = EosGuardRegistry(threshold=2, window_secs=10.0)
        for _ in range(3):
            reg.should_forward("cam_storm_1")
        # cam_storm_1 should be in active storms
        storms = reg.active_storms()
        assert "cam_storm_1" in storms

    def test_independent_storm_per_source(self):
        """Storm ở camera A không ảnh hưởng camera B."""
        reg = EosGuardRegistry(threshold=2, window_secs=10.0)
        # Trigger storm on cam_A
        for _ in range(3):
            reg.should_forward("cam_A")
        assert reg.get("cam_A").is_storm_active is True
        # cam_B should still forward normally
        assert reg.should_forward("cam_B") is True
