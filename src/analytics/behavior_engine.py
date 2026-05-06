"""Behavior analytics engine — Fighting + Camera Tamper detection.

Ported from vms-savant/src/analytics_engine.py với điều chỉnh cho sv-pro:
- FightingDetector và TamperDetector được quản lý per-source_id
  vì sv-pro chạy nhiều camera song song trên cùng 1 pipeline.
- PersonReID bỏ qua — sv-pro đã có ArcFace R100 + pgvector (mạnh hơn OSNet).
- Không có ResultExporter riêng — alert được forward vào BlacklistPyfunc
  (AuditLogger + AlertManager) qua frame attribute "behavior_alerts".
"""

import logging
from collections import deque

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_ort = None


def _get_ort():
    global _ort
    if _ort is None:
        import onnxruntime as ort
        _ort = ort
    return _ort


def _softmax(x):
    e = np.exp(x - np.max(x))
    return e / e.sum()


class FightingDetector:
    """R3D-18 fighting detector — cần 16-frame video clip."""

    def __init__(self, model_path, clip_len=16, frame_size=112,
                 stride=8, threshold=0.6):
        self.clip_len = clip_len
        self.frame_size = frame_size
        self.stride = stride
        self.threshold = threshold
        self.frame_buffer = deque(maxlen=clip_len)
        self.frame_count = 0
        self.last_result = {"fighting": False, "confidence": 0.0}
        # Frame count tại lần inference cuối cùng — dùng để expire stale result.
        # Sau 2×stride frame mà không inference lại → reset về non-fighting.
        self._last_infer_frame = 0

        ort = _get_ort()
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self.session = ort.InferenceSession(model_path, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        logger.info("FightingDetector loaded: %s", model_path)

    def add_frame(self, frame):
        """Thêm frame vào buffer, chạy inference khi đủ clip_len."""
        small = cv2.resize(frame, (self.frame_size, self.frame_size))
        self.frame_buffer.append(small)
        self.frame_count += 1

        if len(self.frame_buffer) == self.clip_len and \
                self.frame_count % self.stride == 0:
            self.last_result = self._infer()
            self._last_infer_frame = self.frame_count

        # Expire stale positive: nếu đã quá 2×stride frame kể từ inference cuối
        # mà last_result vẫn là fighting=True → trả về non-fighting để không spam.
        if self.last_result["fighting"] and \
                (self.frame_count - self._last_infer_frame) >= 2 * self.stride:
            return {"fighting": False, "confidence": self.last_result["confidence"]}

        return self.last_result

    def _infer(self):
        clip = np.stack(list(self.frame_buffer))   # [T, H, W, C]
        clip = clip.astype(np.float32) / 255.0
        clip = np.transpose(clip, (3, 0, 1, 2))    # [C, T, H, W]
        mean = np.array([0.485, 0.456, 0.406]).reshape(3, 1, 1, 1)
        std  = np.array([0.229, 0.224, 0.225]).reshape(3, 1, 1, 1)
        clip = (clip - mean) / std
        clip = clip[np.newaxis].astype(np.float32)  # [1, C, T, H, W]

        logits = self.session.run(None, {self.input_name: clip})[0][0]
        probs  = _softmax(logits)
        # Class 0 = Fight, Class 1 = NonFight
        is_fighting = bool(probs[0] > self.threshold)
        return {"fighting": is_fighting, "confidence": float(probs[0])}


class TamperDetector:
    """Camera tamper classifier — chạy trên full frame mỗi N frame."""

    def __init__(self, model_path, input_size=224, interval=25, threshold=0.7):
        self.input_size = input_size
        self.interval = interval
        self.threshold = threshold
        self.frame_count = 0
        self.last_result = {"tampered": False, "confidence": 0.0}

        ort = _get_ort()
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self.session = ort.InferenceSession(model_path, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        logger.info("TamperDetector loaded: %s", model_path)

    def check(self, frame):
        self.frame_count += 1
        if self.frame_count % self.interval != 0:
            return self.last_result

        img = cv2.resize(frame, (self.input_size, self.input_size))
        img = img.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))          # [C, H, W]
        mean = np.array([0.485, 0.456, 0.406]).reshape(3, 1, 1)
        std  = np.array([0.229, 0.224, 0.225]).reshape(3, 1, 1)
        img  = (img - mean) / std
        img  = img[np.newaxis].astype(np.float32)   # [1, C, H, W]

        probs = self.session.run(None, {self.input_name: img})[0][0]
        probs = _softmax(probs)
        # Class 0 = normal, Class 1 = tampered
        is_tampered = bool(probs[1] > self.threshold)
        self.last_result = {"tampered": is_tampered, "confidence": float(probs[1])}
        return self.last_result


class BehaviorEnginePool:
    """
    Pool quản lý FightingDetector + TamperDetector per-source_id.

    sv-pro có nhiều camera — mỗi source cần buffer riêng để không trộn clip
    frame của camera này vào camera khác.
    """

    def __init__(
        self,
        fighting_model_path: str | None,
        tamper_model_path: str | None,
        fighting_threshold: float = 0.6,
        tamper_threshold: float = 0.7,
        fighting_stride: int = 8,
        tamper_interval: int = 25,
    ):
        self._fighting_path = fighting_model_path
        self._tamper_path   = tamper_model_path
        self._fighting_thr  = fighting_threshold
        self._tamper_thr    = tamper_threshold
        self._fighting_stride   = fighting_stride
        self._tamper_interval   = tamper_interval

        # source_id → detector instance
        self._fighting_pool: dict[str, FightingDetector] = {}
        self._tamper_pool:   dict[str, TamperDetector]   = {}

    # ── public API ────────────────────────────────────────────────────────────

    def process(
        self,
        source_id: str,
        frame: np.ndarray,
        check_fighting: bool = True,
        check_tamper:   bool = True,
    ) -> dict:
        """
        Chạy fighting + tamper detection cho frame thuộc source_id.

        Params:
          check_fighting : False → bỏ qua R3D-18 (cả buffer & inference). Dùng khi
                           không có motion burst (không có ≥2 person gần nhau).
          check_tamper   : False → bỏ qua tamper. Hiếm khi cần tắt.

        Trả về dict:
          {
            "fighting": bool, "fight_confidence": float,
            "tampered": bool, "tamper_confidence": float,
          }
        """
        result = {
            "fighting":          False,
            "fight_confidence":  0.0,
            "tampered":          False,
            "tamper_confidence": 0.0,
        }

        if self._fighting_path and check_fighting:
            detector = self._get_fighting(source_id)
            r = detector.add_frame(frame)
            result["fighting"]         = r["fighting"]
            result["fight_confidence"] = round(r["confidence"], 3)

        if self._tamper_path and check_tamper:
            detector = self._get_tamper(source_id)
            r = detector.check(frame)
            result["tampered"]          = r["tampered"]
            result["tamper_confidence"] = round(r["confidence"], 3)

        return result

    # ── private ───────────────────────────────────────────────────────────────

    def _get_fighting(self, source_id: str) -> FightingDetector:
        if source_id not in self._fighting_pool:
            self._fighting_pool[source_id] = FightingDetector(
                self._fighting_path,
                stride=self._fighting_stride,
                threshold=self._fighting_thr,
            )
        return self._fighting_pool[source_id]

    def _get_tamper(self, source_id: str) -> TamperDetector:
        if source_id not in self._tamper_pool:
            self._tamper_pool[source_id] = TamperDetector(
                self._tamper_path,
                interval=self._tamper_interval,
                threshold=self._tamper_thr,
            )
        return self._tamper_pool[source_id]
