"""BehaviorPyfunc — Savant pipeline stage cho Fighting + Tamper detection.

Vị trí trong pipeline (module.yml):
  ... face_recognizer → behavior_analytics → business_logic ...

Mỗi frame:
  1. Lấy numpy frame từ GstBuffer qua pyds (giống vms-savant AnalyticsEngine).
  2. Chạy BehaviorEnginePool.process(source_id, frame).
  3. Ghi kết quả vào frame tag "behavior_alerts" (JSON) để BlacklistPyfunc đọc.
  4. Ghi object-level attributes cho covered_person / fallen / falling
     (label "behavior_alert" + confidence) lên frame tag để FE overlay.

Nếu pyds không có (môi trường dev) → skip frame-level analytics, không crash.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import pyds
    HAS_PYDS = True
except ImportError:
    HAS_PYDS = False
    logger.warning("pyds not available — BehaviorPyfunc frame analytics disabled")

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    from savant.deepstream.meta.frame import NvDsFrameMeta
    from savant.deepstream.pyfunc import NvDsPyFuncPlugin
except Exception:
    NvDsFrameMeta = object   # type: ignore
    NvDsPyFuncPlugin = object  # type: ignore


class BehaviorPyfunc(NvDsPyFuncPlugin):
    """
    Savant PyFunc stage: phát hiện hành vi nguy hiểm (đánh nhau, tamper camera).

    Config kwargs trong module.yml:
        model_dir:           /models
        fighting_threshold:  0.6
        tamper_threshold:    0.7
        fighting_stride:     8      # chạy inference mỗi N frame
        tamper_interval:     25     # chạy tamper check mỗi N frame
    """

    def __init__(
        self,
        model_dir: str = "/models",
        fighting_threshold: float = 0.6,
        tamper_threshold: float = 0.7,
        fighting_stride: int = 8,
        tamper_interval: int = 25,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._model_dir         = model_dir
        self._fighting_thr      = float(fighting_threshold)
        self._tamper_thr        = float(tamper_threshold)
        self._fighting_stride   = int(fighting_stride)
        self._tamper_interval   = int(tamper_interval)
        self._pool              = None   # BehaviorEnginePool — lazy init on_start

    def on_start(self) -> bool:
        if not super().on_start():
            return False

        model_dir = Path(self._model_dir)
        fighting_path = model_dir / "fighting" / "fighting_detector.onnx"
        tamper_path   = model_dir / "tamper"   / "tamper_classifier.onnx"

        try:
            from src.analytics.behavior_engine import BehaviorEnginePool
            self._pool = BehaviorEnginePool(
                fighting_model_path=str(fighting_path) if fighting_path.exists() else None,
                tamper_model_path=str(tamper_path)     if tamper_path.exists()   else None,
                fighting_threshold=self._fighting_thr,
                tamper_threshold=self._tamper_thr,
                fighting_stride=self._fighting_stride,
                tamper_interval=self._tamper_interval,
            )
            loaded = []
            if fighting_path.exists():
                loaded.append("fighting")
            if tamper_path.exists():
                loaded.append("tamper")
            if loaded:
                logger.info("BehaviorPyfunc loaded models: %s", ", ".join(loaded))
            else:
                logger.warning(
                    "BehaviorPyfunc: no behavior models found in %s — stage passive",
                    model_dir,
                )
        except Exception as exc:
            logger.warning("BehaviorPyfunc init failed (continuing): %s", exc)

        return True

    def process_frame(self, buffer, frame_meta: NvDsFrameMeta) -> None:
        try:
            self._process_frame_safe(buffer, frame_meta)
        except Exception as exc:
            logger.warning("BehaviorPyfunc error: %s", exc)

    def _process_frame_safe(self, buffer, frame_meta: NvDsFrameMeta) -> None:
        if self._pool is None:
            return

        source_id = str(getattr(frame_meta, "source_id", "unknown"))

        # Kiểm tra trước có person-type objects không (nvinfer stages đã chạy).
        # Nếu không có → không cần tốn thời gian lấy frame numpy.
        # Fighting/tamper vẫn cần frame ngay cả khi không có objects.
        has_behavior_objects = any(
            str(getattr(o, "label", "")) in ("person", "covered_person", "fallen", "falling", "standing")
            for o in getattr(frame_meta, "objects", [])
            if not getattr(o, "is_primary", False)
        )
        need_frame = (
            has_behavior_objects
            or (self._pool._fighting_path is not None)
            or (self._pool._tamper_path is not None)
        )
        if not need_frame:
            return

        np_frame  = self._get_frame_numpy(buffer, frame_meta)
        if np_frame is None:
            return

        # ── Motion-burst trigger cho Fighting R3D-18 ─────────────────────────
        # Fighting detection là model 3D Conv nặng (~60ms/clip trên 3060).
        # Chỉ chạy khi có signal thực sự: ≥2 person đứng gần nhau (tương tác
        # vật lý tiềm năng). Scene có 1 người hoặc nhiều người tách xa nhau
        # → bỏ qua fighting, tiết kiệm ~60ms/burst. Tamper vẫn chạy bình
        # thường (static-bg detection không cần motion).
        check_fighting = self._motion_burst_detected(frame_meta)

        result = self._pool.process(
            source_id, np_frame,
            check_fighting=check_fighting,
            check_tamper=True,
        )

        # Chỉ ghi tag khi có alert thực sự để không spam frame metadata
        has_alert = result["fighting"] or result["tampered"]

        # Luôn cần covered_person / fallen / falling từ nvinfer stages →
        # thu thập từ frame_meta.objects để kèm vào tag
        behavior_objects = []
        for obj_meta in getattr(frame_meta, "objects", []):
            label = str(getattr(obj_meta, "label", "") or "")
            if label in ("covered_person", "fallen", "falling"):
                try:
                    bb = obj_meta.bbox
                    behavior_objects.append({
                        "label":      label,
                        "confidence": round(float(obj_meta.confidence), 3),
                        "bbox":       [
                            round(float(bb.left), 1),
                            round(float(bb.top), 1),
                            round(float(bb.left + bb.width), 1),
                            round(float(bb.top + bb.height), 1),
                        ],
                    })
                except Exception:
                    pass

        if not has_alert and not behavior_objects:
            return

        tag_payload = {
            "fighting":          result["fighting"],
            "fight_confidence":  result["fight_confidence"],
            "tampered":          result["tampered"],
            "tamper_confidence": result["tamper_confidence"],
            "behavior_objects":  behavior_objects,
        }

        try:
            frame_meta.set_tag("behavior_alerts", json.dumps(tag_payload, separators=(",", ":")))
        except Exception as exc:
            logger.debug("behavior_alerts tag set failed: %s", exc)

    @staticmethod
    def _collect_person_bboxes(frame_meta) -> list[tuple[float, float, float, float]]:
        out: list[tuple[float, float, float, float]] = []
        try:
            for o in getattr(frame_meta, "objects", []):
                if getattr(o, "is_primary", False):
                    continue
                if str(getattr(o, "label", "")) != "person":
                    continue
                try:
                    bb = o.bbox
                    out.append((
                        float(bb.left),
                        float(bb.top),
                        float(bb.left + bb.width),
                        float(bb.top  + bb.height),
                    ))
                except Exception:
                    continue
        except Exception:
            pass
        return out

    @staticmethod
    def _pair_is_close(a, b) -> bool:
        """IoU > 0 hoặc center distance < 1.5 × avg bbox width."""
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        # IoU > 0
        if max(ax1, bx1) < min(ax2, bx2) and max(ay1, by1) < min(ay2, by2):
            return True
        # Proximity
        aw = ax2 - ax1
        bw = bx2 - bx1
        avg_w = (aw + bw) * 0.5
        if avg_w <= 0:
            return False
        dx = (ax1 + ax2 - bx1 - bx2) * 0.5
        dy = (ay1 + ay2 - by1 - by2) * 0.5
        return (dx * dx + dy * dy) < (1.5 * avg_w) ** 2

    @classmethod
    def _motion_burst_detected(cls, frame_meta) -> bool:
        """
        True khi frame có ≥2 person gần nhau (bbox IoU > 0 hoặc center
        distance < 1.5 × avg bbox width). Proximity proxy cho fighting —
        fighting gần như luôn xảy ra ở close range, signal này đủ gate 95%
        scene 1-người hoặc nhiều-người-tách-xa.
        """
        persons = cls._collect_person_bboxes(frame_meta)
        if len(persons) < 2:
            return False
        for i in range(len(persons)):
            for j in range(i + 1, len(persons)):
                if cls._pair_is_close(persons[i], persons[j]):
                    return True
        return False

    @staticmethod
    def _get_frame_numpy(buffer, frame_meta):
        """
        Trả về frame [H, W, 3] BGR.

        Ưu tiên đọc từ shared_frame_cache (FaceRecognizer đã download ở stage trước)
        → tránh copy VRAM→RAM lần 2. Fallback sang pyds nếu cache miss.
        """
        source_id = str(getattr(frame_meta, "source_id", ""))
        pts = int(getattr(frame_meta, "pts", 0) or 0)

        try:
            from src.shared_frame_cache import get as _cache_get, evict as _cache_evict
            cached = _cache_get(source_id, pts)
            if cached is not None:
                _cache_evict(source_id, pts)   # dùng xong thì xóa ngay
                return cached
        except Exception:
            pass

        # Cache miss → copy trực tiếp từ pyds
        if not HAS_PYDS or not HAS_CV2:
            return None
        try:
            import cv2 as _cv2
            batch_meta    = pyds.gst_buffer_get_nvds_batch_meta(hash(buffer))
            l_frame       = batch_meta.frame_meta_list
            if l_frame is None:
                return None
            ds_frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
            frame = pyds.get_nvds_buf_surface(hash(buffer), ds_frame_meta.batch_id)
            if frame is not None:
                return _cv2.cvtColor(frame, _cv2.COLOR_RGBA2BGR)
        except Exception as exc:
            logger.debug("BehaviorPyfunc frame extract failed: %s", exc)
        return None
