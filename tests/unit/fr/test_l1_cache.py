"""
Unit tests for _L1Cache (LRU cache with TTL) in src/fr/face_recognizer.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import time
import pytest
from src.fr.face_recognizer import _L1Cache


class TestL1Cache:

    def test_put_and_get(self):
        """Basic put-then-get should return the stored data."""
        cache = _L1Cache(capacity=10, ttl=60.0)
        cache.put(1, {"name": "Alice"})
        result = cache.get(1)
        assert result == {"name": "Alice"}

    def test_get_nonexistent_returns_none(self):
        """Getting a key not in cache → None."""
        cache = _L1Cache()
        assert cache.get(999) is None

    def test_ttl_expiry(self):
        """Item should be gone after TTL expires."""
        cache = _L1Cache(capacity=10, ttl=0.05)  # 50ms TTL
        cache.put(1, {"name": "Bob"})
        time.sleep(0.1)  # Wait for TTL to expire
        assert cache.get(1) is None

    def test_within_ttl_still_present(self):
        """Item returned if TTL not yet elapsed."""
        cache = _L1Cache(capacity=10, ttl=5.0)
        cache.put(42, {"role": "staff"})
        result = cache.get(42)
        assert result is not None
        assert result["role"] == "staff"

    def test_capacity_eviction(self):
        """Oldest item evicted when capacity exceeded."""
        cache = _L1Cache(capacity=3, ttl=60.0)
        cache.put(1, "a")
        cache.put(2, "b")
        cache.put(3, "c")
        # Adding 4th item should evict oldest (LRU = first inserted = key 1)
        cache.put(4, "d")
        # key 4 should be present
        assert cache.get(4) == "d"
        # key 1 should be evicted
        assert cache.get(1) is None

    def test_lru_order_updated_on_get(self):
        """Accessing an item should mark it as recently used (not evicted first)."""
        cache = _L1Cache(capacity=3, ttl=60.0)
        cache.put(1, "a")
        cache.put(2, "b")
        cache.put(3, "c")
        # Access key 1 → it becomes most-recently-used
        _ = cache.get(1)
        # Add new item → should evict key 2 (now oldest), not key 1
        cache.put(4, "d")
        assert cache.get(1) is not None  # key 1 still present
        assert cache.get(4) is not None  # key 4 present

    def test_update_existing_key(self):
        """Putting a different value for same key updates it."""
        cache = _L1Cache()
        cache.put(1, {"name": "Alice"})
        cache.put(1, {"name": "AliceUpdated"})
        assert cache.get(1) == {"name": "AliceUpdated"}

    def test_invalidate_removes_key(self):
        """invalidate() should remove the key from cache."""
        cache = _L1Cache()
        cache.put(7, "data")
        cache.invalidate(7)
        assert cache.get(7) is None

    def test_invalidate_nonexistent_no_error(self):
        """invalidate() on missing key should not raise."""
        cache = _L1Cache()
        cache.invalidate(999)  # Should not raise

    def test_zero_capacity_ignored(self):
        """With capacity=0, items still shouldn't cause crashes."""
        cache = _L1Cache(capacity=0, ttl=60.0)
        # Should not crash
        cache.put(1, "x")
