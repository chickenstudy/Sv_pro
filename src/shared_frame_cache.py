"""Shared per-frame numpy cache để tránh nhiều pyfunc stage copy VRAM→RAM độc lập.

Cơ chế:
  - FaceRecognizer (stage 2b) đọc frame → ghi vào cache với key (source_id, pts).
  - BehaviorPyfunc (stage 2c) đọc cache trước, chỉ gọi pyds nếu cache miss.
  - BlacklistPyfunc clear entry sau khi dùng.

TTL = 0: cache chỉ sống 1 frame (key = (source_id, pts) — pts unique per frame).
Không cần lock vì pipeline Savant gọi pyfunc tuần tự trong cùng 1 thread.
"""

from typing import Optional
import numpy as np

# {(source_id, pts): np.ndarray BGR}
_cache: dict[tuple, np.ndarray] = {}
_MAX_ENTRIES = 32   # safety cap: tối đa 32 frame đồng thời (nhiều camera)


def put(source_id: str, pts: int, frame_bgr: np.ndarray) -> None:
    if len(_cache) >= _MAX_ENTRIES:
        # Xóa entry cũ nhất khi đầy
        oldest_key = next(iter(_cache))
        del _cache[oldest_key]
    _cache[(source_id, pts)] = frame_bgr


def get(source_id: str, pts: int) -> Optional[np.ndarray]:
    return _cache.get((source_id, pts))


def evict(source_id: str, pts: int) -> None:
    _cache.pop((source_id, pts), None)
