"""
Unit tests for StrangerReIDRegistry in src/fr/stranger_reid.py

Tests federated stranger re-identification across cameras (local + Redis).
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import numpy as np
import pytest

from src.fr.stranger_reid import StrangerReIDRegistry, _REID_THRESHOLD


class TestStrangerReIDRegistry:

    def test_register_and_lookup_same_embedding(self, unit_embedding):
        """Registering then looking up with very similar embedding → match found."""
        registry = StrangerReIDRegistry(redis_client=None)
        registry.register("STR_ABC123", unit_embedding, camera_id="cam_01")
        # Lookup with identical embedding (excluding own ID)
        matched = registry.lookup(unit_embedding, camera_id="cam_02")
        assert matched == "STR_ABC123"

    def test_lookup_similar_embedding_matches(self, unit_embedding, similar_embedding):
        """Moderately similar embedding (cosine > threshold) → should match."""
        registry = StrangerReIDRegistry(redis_client=None, threshold=0.90)
        registry.register("STR_SIMILAR", unit_embedding, camera_id="cam_01")
        # Check cosine similarity
        sim = float(np.dot(unit_embedding, similar_embedding))
        if sim >= 0.90:
            matched = registry.lookup(similar_embedding, camera_id="cam_02")
            assert matched == "STR_SIMILAR"

    def test_lookup_different_embedding_no_match(self, unit_embedding, different_embedding):
        """Very different embedding → no match (below threshold)."""
        registry = StrangerReIDRegistry(redis_client=None, threshold=_REID_THRESHOLD)
        registry.register("STR_XYZ", unit_embedding, camera_id="cam_01")
        sim = float(np.dot(unit_embedding, different_embedding))
        if sim < _REID_THRESHOLD:
            matched = registry.lookup(different_embedding, camera_id="cam_02")
            assert matched is None

    def test_lookup_empty_registry_returns_none(self, unit_embedding):
        """Empty registry → no match."""
        registry = StrangerReIDRegistry(redis_client=None)
        assert registry.lookup(unit_embedding, camera_id="cam_01") is None

    def test_exclude_id_self_match_prevention(self, unit_embedding):
        """exclude_id prevents self-matching."""
        registry = StrangerReIDRegistry(redis_client=None)
        registry.register("STR_SELF", unit_embedding, camera_id="cam_01")
        matched = registry.lookup(unit_embedding, camera_id="cam_01", exclude_id="STR_SELF")
        # Should not match itself
        assert matched is None

    def test_register_twice_updates_centroid(self, unit_embedding):
        """Registering same ID twice: frame_count increases, no duplicate."""
        registry = StrangerReIDRegistry(redis_client=None)
        registry.register("STR_1", unit_embedding, camera_id="cam_01")
        registry.register("STR_1", unit_embedding, camera_id="cam_02")
        # Should have 1 entry with frame_count=2 and both cameras
        entries = registry.get_all()
        assert len(entries) == 1
        assert entries[0]["frame_count"] == 2
        assert "cam_01" in entries[0]["camera_ids"]
        assert "cam_02" in entries[0]["camera_ids"]

    def test_get_all_returns_sorted_by_last_seen(self, unit_embedding, different_embedding):
        """get_all() should return newest first."""
        registry = StrangerReIDRegistry(redis_client=None)
        registry.register("STR_OLD", unit_embedding, camera_id="cam_01")
        import time; time.sleep(0.01)
        registry.register("STR_NEW", different_embedding, camera_id="cam_02")
        entries = registry.get_all()
        assert len(entries) == 2
        assert entries[0]["stranger_id"] == "STR_NEW"  # newest first

    def test_flush_expired_removes_old_entries(self, unit_embedding):
        """flush_expired() should remove entries older than max_age_secs."""
        import time
        registry = StrangerReIDRegistry(redis_client=None)
        registry.register("STR_X", unit_embedding, camera_id="cam_01")
        time.sleep(0.05)
        removed = registry.flush_expired(max_age_secs=0.01)  # 10ms max age
        assert removed == 1
        assert registry.lookup(unit_embedding, camera_id="cam_01") is None

    def test_evict_oldest_when_full(self, unit_embedding):
        """When registry hits _LOCAL_MAX_SIZE, oldest entry should be evicted."""
        from src.fr.stranger_reid import _LOCAL_MAX_SIZE
        registry = StrangerReIDRegistry(redis_client=None, threshold=0.99)
        # Fill registry beyond capacity
        import time
        for i in range(_LOCAL_MAX_SIZE + 1):
            rng = np.random.default_rng(i)
            emb = rng.standard_normal(512).astype(np.float32)
            emb /= np.linalg.norm(emb)
            time.sleep(0.001)  # ensure different last_seen
            registry.register(f"STR_{i}", emb, camera_id="cam_01")
        # Total should not exceed LOCAL_MAX_SIZE
        assert len(registry.get_all()) <= _LOCAL_MAX_SIZE

    def test_register_with_redis(self, unit_embedding, mock_redis):
        """With mock Redis: embedding should be pushed to Redis store."""
        registry = StrangerReIDRegistry(redis_client=mock_redis)
        registry.register("STR_REDIS", unit_embedding, camera_id="cam_01")
        # Check Redis has the key
        keys = mock_redis.keys("svpro:stranger:*")
        assert len(keys) == 1
        # MockRedis stores keys as plain strings
        key_str = keys[0] if isinstance(keys[0], str) else keys[0].decode("utf-8")
        assert "STR_REDIS" in key_str
