"""
Stranger Re-ID Đa Camera — Sprint 4 Task 3.7.

Mục tiêu:
  Khi một người lạ (stranger) xuất hiện ở camera A và sau đó camera B phát hiện
  người có embedding tương tự → nhận dạng là cùng một người (Federated Re-ID).

Kiến trúc:
  - StrangerRegistry lưu embedding đại diện (centroid) của mỗi stranger.
  - Khi FaceRecognizer phát hiện stranger mới → gọi .register().
  - Sau đó, mỗi lần có embedding mới không khớp DB → gọi .lookup() trước khi
    tạo stranger_id mới, nếu match → dùng lại stranger_id cũ (same identity).
  - Registry được share giữa các camera qua Redis (key: svpro:stranger:<id>).
  - TTL Redis: 30 phút (người lạ có thể xuất hiện lại sau 30 phút).

Thread-safety:
  - Dùng threading.Lock nội bộ cho local map.
  - Redis operation atomic (SETNX + EXPIRE).
"""

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Ngưỡng nhận diện lại stranger ────────────────────────────────────────────
_REID_THRESHOLD   = 0.50    # Cosine sim tối thiểu để coi là cùng 1 người
_STRANGER_TTL_S   = 1800    # Thời gian sống của stranger trong Redis (30 phút)
_LOCAL_MAX_SIZE   = 500     # Giới hạn local registry để tránh tràn RAM

# ── Redis key prefix ──────────────────────────────────────────────────────────
_REDIS_PREFIX = "svpro:stranger:"


@dataclass
class StrangerEntry:
    """Thông tin một stranger đã được đăng ký vào Re-ID registry."""
    stranger_id:     str
    centroid:        np.ndarray    # Embedding đại diện (trung bình các frame)
    camera_ids:      list[str]     # Danh sách camera đã thấy
    last_seen:       float         # time.monotonic()
    frame_count:     int = 1
    extra:           dict = field(default_factory=dict)


class StrangerReIDRegistry:
    """
    Registry nhận dạng lại người lạ đa camera (Federated Stranger Re-ID).

    Cách dùng trong FaceRecognizer:
      # Sau khi có embedding của stranger:
      matched_id = registry.lookup(embedding, camera_id)
      if matched_id:
          stranger_id = matched_id  # Dùng lại ID cũ
      else:
          stranger_id = new_id      # Tạo stranger ID mới
          registry.register(stranger_id, embedding, camera_id)
    """

    def __init__(self, redis_client=None, threshold: float = _REID_THRESHOLD):
        # Local map: stranger_id → StrangerEntry
        self._registry: dict[str, StrangerEntry] = {}
        self._lock      = threading.Lock()
        self._redis     = redis_client
        self._threshold = threshold

    def register(
        self,
        stranger_id: str,
        embedding:   np.ndarray,
        camera_id:   str,
        extra:       dict | None = None,
    ) -> None:
        """
        Đăng ký một stranger mới vào registry.
        Nếu đã tồn tại → cập nhật centroid (moving average) và camera list.
        Thread-safe.
        """
        with self._lock:
            existing = self._registry.get(stranger_id)
            if existing:
                # Cập nhật centroid bằng online moving average
                n = existing.frame_count
                existing.centroid = (existing.centroid * n + embedding) / (n + 1)
                # Normalize lại L2
                norm = np.linalg.norm(existing.centroid)
                if norm > 1e-6:
                    existing.centroid /= norm
                existing.frame_count += 1
                existing.last_seen = time.monotonic()
                if camera_id not in existing.camera_ids:
                    existing.camera_ids.append(camera_id)
            else:
                # Giới hạn kích thước registry
                if len(self._registry) >= _LOCAL_MAX_SIZE:
                    self._evict_oldest()
                self._registry[stranger_id] = StrangerEntry(
                    stranger_id = stranger_id,
                    centroid    = embedding.copy(),
                    camera_ids  = [camera_id],
                    last_seen   = time.monotonic(),
                    extra       = extra or {},
                )

        # Ghi lên Redis để share với các instance khác
        self._push_to_redis(stranger_id, embedding, camera_id)

    def lookup(
        self,
        embedding:  np.ndarray,
        camera_id:  str,
        exclude_id: str | None = None,
    ) -> Optional[str]:
        """
        Tìm kiếm stranger có embedding gần nhất với embedding đầu vào.
        Tra theo thứ tự:
          1. Local registry (nhanh, in-process).
          2. Redis registry (shared giữa các camera worker).

        Trả về stranger_id nếu tìm được match, ngược lại None.

        Args:
            embedding:  Embedding 512-dim đã L2-normalized.
            camera_id:  Camera đang xử lý (để log).
            exclude_id: Bỏ qua ID này khi tìm kiếm (tránh self-match).
        """
        # ── Tìm trong local registry ──────────────────────────────────────────
        best_id    = None
        best_score = 0.0

        with self._lock:
            for sid, entry in self._registry.items():
                if sid == exclude_id:
                    continue
                sim = float(np.dot(embedding, entry.centroid))
                if sim > best_score:
                    best_score = sim
                    best_id    = sid

        if best_id and best_score >= self._threshold:
            logger.debug(
                "Stranger Re-ID (local): %s matched stranger %s sim=%.3f cam=%s",
                exclude_id or "new", best_id, best_score, camera_id,
            )
            return best_id

        # ── Fallback: tìm trong Redis ─────────────────────────────────────────
        redis_id = self._lookup_redis(embedding, exclude_id)
        if redis_id:
            logger.info(
                "Stranger Re-ID (Redis): cam=%s matched stranger %s cross-camera",
                camera_id, redis_id,
            )
        return redis_id

    def get_all(self) -> list[dict]:
        """
        Trả về danh sách tất cả stranger đang trong local registry.
        Dùng cho API /api/strangers để build Gallery.
        """
        with self._lock:
            return [
                {
                    "stranger_id":  e.stranger_id,
                    "camera_ids":   e.camera_ids,
                    "frame_count":  e.frame_count,
                    "last_seen":    e.last_seen,
                    "extra":        e.extra,
                }
                for e in sorted(
                    self._registry.values(),
                    key=lambda x: x.last_seen, reverse=True,
                )
            ]

    def flush_expired(self, max_age_secs: float = _STRANGER_TTL_S) -> int:
        """
        Xóa các stranger không xuất hiện lại sau max_age_secs giây.
        Gọi định kỳ (ví dụ mỗi 5 phút) để giải phóng bộ nhớ.
        Trả về số lượng đã xóa.
        """
        now = time.monotonic()
        cutoff = now - max_age_secs
        with self._lock:
            expired = [sid for sid, e in self._registry.items() if e.last_seen < cutoff]
            for sid in expired:
                del self._registry[sid]
        if expired:
            logger.info("StrangerReID: evicted %d expired entries", len(expired))
        return len(expired)

    # ── Private helpers ────────────────────────────────────────────────────────

    def _push_to_redis(
        self,
        stranger_id: str,
        embedding:   np.ndarray,
        camera_id:   str,
    ) -> None:
        """Ghi embedding và metadata stranger lên Redis để share đa camera."""
        if not self._redis:
            return
        try:
            key = f"{_REDIS_PREFIX}{stranger_id}"
            payload = json.dumps({
                "stranger_id": stranger_id,
                "embedding":   embedding.tolist(),
                "camera_id":   camera_id,
                "ts":          time.time(),
            })
            self._redis.setex(key, _STRANGER_TTL_S, payload)
        except Exception as exc:
            logger.debug("Stranger Redis push failed: %s", exc)

    def _lookup_redis(
        self,
        embedding:  np.ndarray,
        exclude_id: str | None = None,
    ) -> Optional[str]:
        """
        Tìm kiếm stranger trong Redis.
        Brute-force cosine similarity trên tất cả key svpro:stranger:*.
        """
        if not self._redis:
            return None
        try:
            keys = self._redis.keys(f"{_REDIS_PREFIX}*")
            best_id    = None
            best_score = 0.0

            for key in keys:
                raw = self._redis.get(key)
                if not raw:
                    continue
                data = json.loads(raw)
                sid  = data.get("stranger_id", "")
                if sid == exclude_id:
                    continue
                ref_emb = np.array(data["embedding"], dtype=np.float32)
                sim = float(np.dot(embedding, ref_emb))
                if sim > best_score:
                    best_score = sim
                    best_id    = sid

            if best_id and best_score >= self._threshold:
                return best_id
        except Exception as exc:
            logger.debug("Stranger Redis lookup failed: %s", exc)
        return None

    def _evict_oldest(self) -> None:
        """Xóa entry cũ nhất nếu registry đầy (gọi khi đã giữ lock)."""
        if not self._registry:
            return
        oldest_id = min(self._registry, key=lambda k: self._registry[k].last_seen)
        del self._registry[oldest_id]
        logger.debug("StrangerReID: evicted oldest entry %s", oldest_id)


# ── Singleton ─────────────────────────────────────────────────────────────────
# Khởi tạo kết nối Redis sau khi FaceRecognizer gọi _init_redis().
# stranger_registry.redis = recognizer._redis
stranger_registry = StrangerReIDRegistry()
