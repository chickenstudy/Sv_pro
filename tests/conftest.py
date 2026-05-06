"""
Shared fixtures and mock stubs for SV-PRO test suite.

IMPORTANT: We mock all heavy dependencies (Savant, ONNX, Redis, psycopg2)
at the TOP of conftest.py so that src modules that import these at
module-level can be safely imported in a plain Python environment
(no GPU / DeepStream / Savant installed).

Lưu ý: paddleocr stub vẫn giữ vì test pre-migration có thể import.
plate_ocr.py hiện KHÔNG còn import paddleocr — stub chỉ là safety net.
"""

import sys
import types
from unittest.mock import MagicMock

# ─── Stub out Savant / DeepStream dependencies ────────────────────────────────
# plate_ocr.py and face_recognizer.py import savant at the top level.
# We replace the entire package tree with MagicMock before any test imports.

def _make_mock_module(name: str):
    mod = types.ModuleType(name)
    mod.__spec__ = None
    return mod

for _mod_name in [
    "savant",
    "savant.deepstream",
    "savant.deepstream.meta",
    "savant.deepstream.meta.frame",
    "savant.deepstream.pyfunc",
    "savant.deepstream.opencv_utils",
]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = _make_mock_module(_mod_name)

# Add the specific classes that src modules reference at import time
_frame_meta_mock = MagicMock()
sys.modules["savant.deepstream.meta.frame"].NvDsFrameMeta = _frame_meta_mock  # type: ignore
sys.modules["savant.deepstream.pyfunc"].NvDsPyFuncPlugin = object  # type: ignore[attr-defined]
sys.modules["savant.deepstream.opencv_utils"].nvds_to_gpu_mat = MagicMock()  # type: ignore[attr-defined]

# ─── Stub out PaddleOCR ────────────────────────────────────────────────────────
if "paddleocr" not in sys.modules:
    _paddleocr_mod = _make_mock_module("paddleocr")
    _paddleocr_mod.PaddleOCR = MagicMock  # type: ignore[attr-defined]
    sys.modules["paddleocr"] = _paddleocr_mod

# ─── Stub out onnxruntime ─────────────────────────────────────────────────────
if "onnxruntime" not in sys.modules:
    _ort_mod = _make_mock_module("onnxruntime")
    _ort_mod.InferenceSession = MagicMock  # type: ignore[attr-defined]
    sys.modules["onnxruntime"] = _ort_mod

# ─── Stub out psycopg2 ────────────────────────────────────────────────────────
if "psycopg2" not in sys.modules:
    _psycopg2_mod = _make_mock_module("psycopg2")
    _psycopg2_mod.connect = MagicMock()  # type: ignore[attr-defined]
    sys.modules["psycopg2"] = _psycopg2_mod

# psycopg2.extensions — cần cho go2rtc_sync.py và rtsp_ingest.py
if "psycopg2.extensions" not in sys.modules:
    _psycopg2_ext = _make_mock_module("psycopg2.extensions")
    _psycopg2_ext.ISOLATION_LEVEL_AUTOCOMMIT = 0  # type: ignore[attr-defined]
    sys.modules["psycopg2.extensions"] = _psycopg2_ext
    # Gắn vào package cha để `import psycopg2.extensions` hoạt động
    sys.modules["psycopg2"].extensions = _psycopg2_ext  # type: ignore[attr-defined]

# psycopg2.pool — cần cho BlacklistEngine và AuditLogger (ThreadedConnectionPool)
if "psycopg2.pool" not in sys.modules:
    _psycopg2_pool = _make_mock_module("psycopg2.pool")
    _psycopg2_pool.ThreadedConnectionPool = MagicMock  # type: ignore[attr-defined]
    sys.modules["psycopg2.pool"] = _psycopg2_pool
    sys.modules["psycopg2"].pool = _psycopg2_pool  # type: ignore[attr-defined]

# ─── Stub out redis ───────────────────────────────────────────────────────────
if "redis" not in sys.modules:
    _redis_mod = _make_mock_module("redis")
    _redis_mod.Redis = MagicMock  # type: ignore[attr-defined]
    sys.modules["redis"] = _redis_mod

import time
from collections import OrderedDict
from typing import Generator
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ─── Image fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def blank_bgr_image():
    """128x128 blank black BGR image."""
    return np.zeros((128, 128, 3), dtype=np.uint8)


@pytest.fixture
def bright_bgr_image():
    """128x128 white BGR image (high brightness)."""
    return np.full((128, 128, 3), 220, dtype=np.uint8)


@pytest.fixture
def sharp_face_image():
    """112x112 synthetic face image with edges → high Laplacian variance."""
    img = np.zeros((112, 112, 3), dtype=np.uint8)
    # Add hard edges (checkerboard) → high sharpness
    img[::8, :] = 200
    img[:, ::8] = 200
    return img


@pytest.fixture
def blurry_face_image():
    """112x112 uniform gray image → near-zero Laplacian variance."""
    return np.full((112, 112, 3), 128, dtype=np.uint8)


@pytest.fixture
def dark_face_image():
    """112x112 very dark image (simulates night, low illumination)."""
    return np.full((112, 112, 3), 20, dtype=np.uint8)


@pytest.fixture
def plate_bgr_image():
    """Synthetic 64x256 license plate crop (blank)."""
    img = np.full((64, 256, 3), 200, dtype=np.uint8)
    return img


# ─── Landmark fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def frontal_landmarks():
    """5-point landmarks for a frontal face (yaw≈0, pitch≈0)."""
    return np.array([
        [38.0, 52.0],   # left eye
        [74.0, 52.0],   # right eye
        [56.0, 72.0],   # nose
        [42.0, 92.0],   # left mouth
        [70.0, 92.0],   # right mouth
    ], dtype=np.float32)


@pytest.fixture
def turned_landmarks():
    """5-point landmarks for a turned face (high yaw)."""
    return np.array([
        [20.0, 52.0],   # left eye (close to center — face turned right)
        [74.0, 52.0],   # right eye
        [80.0, 72.0],   # nose (far right → large yaw)
        [25.0, 92.0],   # left mouth
        [75.0, 92.0],   # right mouth
    ], dtype=np.float32)


# ─── Embedding fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def unit_embedding():
    """Random L2-normalized 512-dim embedding."""
    rng = np.random.default_rng(42)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def similar_embedding(unit_embedding):
    """Embedding that is very similar to unit_embedding (cosine sim ≈ 0.98)."""
    rng = np.random.default_rng(99)
    noise = rng.standard_normal(512).astype(np.float32) * 0.05
    v = unit_embedding + noise
    return v / np.linalg.norm(v)


@pytest.fixture
def different_embedding():
    """Embedding orthogonal (or near-orthogonal) to unit_embedding."""
    rng = np.random.default_rng(7)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


# ─── Mock Redis ───────────────────────────────────────────────────────────────

class MockRedis:
    """Simple in-memory mock for redis.Redis — supports get/set/setex/keys/delete/pipeline."""

    def __init__(self):
        self._store: dict[str, bytes] = {}
        self._expires: dict[str, float] = {}

    def _is_alive(self, key: str) -> bool:
        exp = self._expires.get(key)
        if exp and time.monotonic() > exp:
            self._store.pop(key, None)
            self._expires.pop(key, None)
            return False
        return key in self._store

    def get(self, key: str):
        return self._store.get(key) if self._is_alive(key) else None

    def set(self, key: str, value):
        self._store[key] = value if isinstance(value, bytes) else str(value).encode()

    def setex(self, key: str, ttl_secs: int, value):
        self.set(key, value)
        self._expires[key] = time.monotonic() + ttl_secs

    def delete(self, *keys):
        for k in keys:
            self._store.pop(k, None)
            self._expires.pop(k, None)

    def keys(self, pattern: str = "*"):
        import fnmatch
        return [k for k in self._store if self._is_alive(k) and fnmatch.fnmatch(k, pattern)]

    def ping(self):
        return True

    def pipeline(self, transaction=True):
        return self  # simplified: pipeline === self

    def execute(self):
        pass


@pytest.fixture
def mock_redis():
    """Fresh MockRedis instance per test."""
    return MockRedis()


# ─── Mock DB (psycopg2) ───────────────────────────────────────────────────────

@pytest.fixture
def mock_db_conn():
    """Mock psycopg2 connection + cursor that returns empty results by default."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    conn.cursor.return_value = cursor
    return conn, cursor
