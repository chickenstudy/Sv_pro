"""
License Plate Recognition pyfunc for Savant pipeline.

Two-stage:
  1. Plate detection  – YOLOv8n ONNX via ONNX Runtime (CPU)
  2. Plate OCR        – PaddleOCR v4 with lang='en' (CPU)

Operates on vehicle objects (car / truck / bus / motorcycle) already
detected by the primary YOLOv8s detector.  Reads the plate text and
attaches it as an attribute that the draw function and JSON egress
will forward downstream.
"""

import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import json
import re

import cv2
import numpy as np

from savant.deepstream.meta.frame import NvDsFrameMeta
from savant.deepstream.pyfunc import NvDsPyFuncPlugin

logger = logging.getLogger(__name__)

# Base directory for saving detected crops
_DETECT_BASE_DIR = "/Detect"

# Vietnam timezone (UTC+7)
_VN_TZ = timezone(timedelta(hours=7))

# Vietnamese plate uses a restricted Latin + digit set
_PLATE_ALLOWED = frozenset("0123456789ABCDEFGHJKLMNPRSTUVWXYZ-")

# Labels produced by the primary vehicle detector
_VEHICLE_LABELS = frozenset({"car", "truck", "bus", "motorcycle"})

# Attribute namespace written to object metadata
_ATTR_ELEMENT = "lpr"
_ATTR_NAME = "plate_number"

# Per-line OCR confidence for line detection (lower than final output threshold).
# Allows dim/blurry top lines on 2-line motorcycle plates to still be collected.
_LINE_DETECT_CONF = 0.35

# Vietnamese plate classification patterns (order matters: most specific first)
#
# XE_MAY_DIEN   : xe máy/xe điện từ 2024 — region + separator + 2 letters +
#                 separator + 5 digits (e.g. 29-EF-12345). Must come before
#                 BIEN_CA_NHAN which also allows 2-letter + 5-digit format.
# XE_MAY_DAN_SU : 4th char must be digit 1-9 (VN motorcycle series: A1, B2 …)
# O_TO_DAN_SU   : single-letter series (30A-) OR two-char series where 4th char
#                 is a letter or '0' (98LD-, 98L0-, 99A- …)
# BIEN_CA_NHAN  : region + separator + 1-2 letters + separator + 3-5 digits
_PLATE_CATEGORIES: list[tuple[str, re.Pattern]] = [
    ("XE_MAY_DAN_SU", re.compile(r'^([1-9][0-9])\s?(-|\.)\s?([A-Z]{2}|[A-Z][0-9])\s?(-|\.)\s?(\d{3}\.?\d{2}|\d{5})$')),
    ("XE_MAY_DAN_SU", re.compile(r'^[1-9][0-9]\s?[-.]?\s?[A-Z][1-9]\s?[-.]?\s?(\d{3}\.?\d{2}|\d{4,5})$')),
    ("O_TO_DAN_SU",   re.compile(r'^[1-9][0-9][A-Z][A-Z]?\s?[-.]?\s?(\d{3}\.?\d{2}|\d{4,5})$')),
    ("BIEN_CA_NHAN",  re.compile(r'^([1-9][0-9])\s?[-.]?\s?([A-Z]{1,2}|NG|QT|NN|CV)\s?[-.]?\s?(\d{3,5}|\d{3}\.?\d{2})$')),
    ("XE_QUAN_DOI",   re.compile(r'^(AA|BB|BC|BK|BH|BT|BP|BS|BV|CB|CK|DA|DB|DC|HA|HB|HC|HE|HT|HQ|KA|KB|KC|KD|KV|KP|KT|KN|PA|PB|PY|PK|PM|PP|PX|QA|QB|QC|QD|QM|QP|QS|QT|TC|TH|TK|TM|TN|TP|TR|TT|TY|UA|UB|UC|UD|UG|UH|UK|UL|UM|UN|UP|UU|UV|UX|VC|VK|VT|VX)\s?[-.]?\s?(\d{3}\.?\d{2}|\d{4,5})$')),
]
_CATEGORY_UNKNOWN = "KHONG_XAC_DINH"

# ── Object tracking ───────────────────────────────────────────────────────────

# A track with no new detection for this many seconds is considered expired.
_TRACK_MAX_AGE = 2.0

# Don't save the same plate number twice within this window (seconds).
_PLATE_DEDUP_SECS = 60.0


# Plate crops with Laplacian variance below this are too blurry for reliable OCR.
_MIN_PLATE_SHARPNESS = 25.0

# Maximum OCR candidates buffered per track for character-level voting.
_MAX_TRACK_CANDIDATES = 10

# Mean brightness below this threshold → treat frame as nighttime.
_NIGHT_BRIGHTNESS_THRESH = 80

# Subdirectory name for vehicles where plate detection failed.
_NOT_DETECTED_DIR = "NOT_DETECTED"

# Only save NOT_DETECTED crops this often per (source, track_id) to avoid flooding.
_NOT_DETECTED_INTERVAL_SECS = 30.0


def _mean_brightness(bgr: np.ndarray) -> float:
    """Return mean pixel brightness (0–255) of a BGR image."""
    return float(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).mean())


def _gamma_correct(bgr: np.ndarray, gamma: float) -> np.ndarray:
    """Apply gamma correction: <1 brightens, >1 darkens."""
    inv = 1.0 / gamma
    lut = (np.arange(256, dtype=np.float32) / 255.0) ** inv * 255.0
    lut = np.clip(lut, 0, 255).astype(np.uint8)
    return lut[bgr]


def _sharpness(bgr: np.ndarray) -> float:
    """Laplacian variance — higher means sharper image."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var()


def _vote_plate(candidates: list[tuple[str, float]]) -> tuple[str | None, float]:
    """
    Character-level majority vote across multiple OCR reads of the same plate.

    1. Strip separators (-, .) for positional alignment.
    2. Group by stripped length — use the most common length.
    3. Vote per character position within the group.
    4. Re-normalize the voted string through the same pipeline.

    Returns (plate_text, avg_confidence) or (None, 0.0).
    """
    if not candidates:
        return None, 0.0
    if len(candidates) == 1:
        return candidates[0]

    from collections import Counter

    stripped = [(re.sub(r'[-.\s]', '', t), c) for t, c in candidates]

    # Most common stripped length = canonical format
    best_len = Counter(len(s) for s, _ in stripped).most_common(1)[0][0]
    group = [(s, c) for s, c in stripped if len(s) == best_len]

    # Vote per character position
    voted = [
        Counter(s[i] for s, _ in group).most_common(1)[0][0]
        for i in range(best_len)
    ]
    voted_text = ''.join(voted)
    avg_conf = sum(c for _, c in group) / len(group)

    normalized = _normalize_plate(voted_text)
    return normalized, avg_conf


class _TrackState:
    """Tracks a single vehicle instance across frames."""

    __slots__ = (
        "track_id", "bbox", "label", "last_seen",
        "ocr_candidates",
        "best_plate", "best_plate_category",
        "best_ocr_conf", "best_det_conf",
        "best_vehicle_crop", "best_plate_crop",
        "best_vehicle_bbox", "best_plate_bbox",
    )

    def __init__(self, track_id: int, bbox: tuple, label: str, last_seen: float):
        self.track_id = track_id
        self.bbox = bbox
        self.label = label
        self.last_seen = last_seen
        self.ocr_candidates: list[tuple[str, float]] = []
        self.best_plate: str | None = None
        self.best_plate_category: str | None = None
        self.best_ocr_conf: float = 0.0
        self.best_det_conf: float = 0.0
        self.best_vehicle_crop = None
        self.best_plate_crop = None
        self.best_vehicle_bbox: tuple | None = None
        self.best_plate_bbox: tuple | None = None

    def update_best(
        self,
        plate: str,
        plate_category: str,
        ocr_conf: float,
        det_conf: float,
        vehicle_crop,
        plate_crop,
        vehicle_bbox: tuple,
        plate_bbox: tuple,
    ) -> None:
        """Buffer candidate for voting; keep best crops from highest-conf frame."""
        # Always add to voting pool (capped at _MAX_TRACK_CANDIDATES)
        if len(self.ocr_candidates) < _MAX_TRACK_CANDIDATES:
            self.ocr_candidates.append((plate, ocr_conf))

        # Keep crops/metadata from the highest-confidence frame
        if ocr_conf > self.best_ocr_conf:
            self.best_plate = plate
            self.best_plate_category = plate_category
            self.best_ocr_conf = ocr_conf
            self.best_det_conf = det_conf
            self.best_vehicle_crop = vehicle_crop.copy()
            self.best_plate_crop = plate_crop.copy()
            self.best_vehicle_bbox = vehicle_bbox
            self.best_plate_bbox = plate_bbox


def _classify_plate(text: str) -> str:
    """Return the category label for a plate number, or KHONG_XAC_DINH."""
    for category, pat in _PLATE_CATEGORIES:
        if pat.match(text):
            return category
    return _CATEGORY_UNKNOWN


def _normalize_plate(text: str) -> str:
    """
    Normalize OCR output to a valid Vietnamese plate string.

    Strategy (applied in order, first match wins):
    1. Try the text as-is.
    2. Replace letter 'O' with 'D' — O is not used in VN plates; in the
       series/letter section OCR often confuses D↔O.
    3. Replace letter 'O' with '0' — in the digit section OCR confuses 0↔O.
    4. Strip up to 2 leading garbage characters (e.g. '730L-' → '30L-').
    Each variant also removes any residual 'O' not in _PLATE_ALLOWED before
    classification.
    """
    def _try(candidate: str) -> str:
        # Remove any leftover 'O' that wasn't substituted
        clean = "".join(c for c in candidate if c in _PLATE_ALLOWED)
        return clean if _classify_plate(clean) != _CATEGORY_UNKNOWN else clean

    # Targeted: digit '0' immediately after region+letter (series pos) → 'D'
    # e.g. '98L0-00558' → '98LD-00558'. Only prepend if it actually changes text.
    _series_zero = re.sub(r'^([1-9][0-9][A-Z])0', r'\1D', text)

    # OCR digit↔letter confusions at the series position (right after region code).
    # Covers both 3-part separator format (29-X1-...) and concatenated (29X1-...).
    # '1' → 'T'  e.g. '29-15-35714' → '29-T5-35714'
    # '0' → 'D'  e.g. '29-01-99642' → '29-D1-99642'
    # '6' → 'G'  e.g. '29-61-66398' → '29-G1-66398'
    # '7' → 'T'  e.g. '2975-74922'  → '29T5-74922', '29-71-06545' → '29-T1-06545'
    # '8' → 'B'  e.g. '238-06729'   → '23B-06729',  '29-81-35278' → '29-B1-35278'
    _s1T = re.sub(r'^([1-9][0-9]\s?[-.]?\s?)1([0-9]\s?[-.]?\s?\d)', r'\g<1>T\2', text)
    _s0D = re.sub(r'^([1-9][0-9]\s?[-.]?\s?)0(?=[-.\s]?[1-9])',      r'\g<1>D',   text)
    _s6G = re.sub(r'^([1-9][0-9]\s?[-.]?\s?)6(?=[-.\dA-Z])',          r'\g<1>G',   text)
    _s7T = re.sub(r'^([1-9][0-9]\s?[-.]?\s?)7(?=[-.\dA-Z])',          r'\g<1>T',   text)
    _s8B = re.sub(r'^([1-9][0-9]\s?[-.]?\s?)8(?=[-.\dA-Z])',          r'\g<1>B',   text)

    # Strip one spurious leading digit from the number section when 6 digits detected.
    # e.g. '80A-104462' → '80A-04462'  (OCR reads plate border as extra '1')
    _strip1 = re.sub(r'^([1-9][0-9][A-Z]{1,2}\s?[-.]?\s?)1(\d{4,5})$', r'\1\2', text)

    variants = [
        text.replace('O', 'D'),   # letter O in series → D  (try first)
        text.replace('O', '0'),   # letter O in digit section → 0
        text,                     # original — O removed by PLATE_ALLOWED filter
    ]
    for sub in (_series_zero, _s1T, _s0D, _s6G, _s7T, _s8B, _strip1):
        if sub != text:
            variants.insert(0, sub)
    for strip in range(3):
        for v in variants:
            candidate = "".join(c for c in v[strip:] if c in _PLATE_ALLOWED)
            if _classify_plate(candidate) != _CATEGORY_UNKNOWN:
                return candidate

    # No match found — return original with invalid chars stripped
    return "".join(c for c in text if c in _PLATE_ALLOWED)


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thresh: float) -> list[int]:
    """Simple NMS, returns indices to keep."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while len(order):
        i = order[0]
        keep.append(int(i))
        if len(order) == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        order = order[1:][iou < iou_thresh]
    return keep


class PlateOCR(NvDsPyFuncPlugin):
    """
    Savant pyfunc: detect and read Vietnamese license plates on vehicles.

    Parameters
    ----------
    plate_model_path : str
        Path to the YOLOv8n plate-detection ONNX file inside the container.
    plate_conf_threshold : float
        Minimum confidence to accept a plate detection (default 0.50).
    nms_iou_threshold : float
        IoU threshold for plate NMS (default 0.45).
    ocr_conf_threshold : float
        Minimum PaddleOCR character confidence (default 0.60).
    roi_zones : dict[str, list[int]] | None
        Per-source ROI zones as {source_id: [x1, y1, x2, y2]}.
        Vehicles whose center falls outside the zone are skipped.
        If a source is not listed, all vehicles are processed (no filter).
    """

    def __init__(
        self,
        plate_model_path: str = "/models/yolov8s_plate/yolov8s_plate.onnx",
        plate_conf_threshold: float = 0.50,
        nms_iou_threshold: float = 0.45,
        ocr_conf_threshold: float = 0.60,
        roi_zones: "dict[str, list[int]] | None" = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.plate_model_path = plate_model_path
        self.plate_conf_threshold = plate_conf_threshold
        self.nms_iou_threshold = nms_iou_threshold
        self.ocr_conf_threshold = ocr_conf_threshold
        # {source_id: (x1, y1, x2, y2)}
        self._roi_zones: dict[str, tuple[int, int, int, int]] = {
            k: tuple(v) for k, v in (roi_zones or {}).items()
        }

        self._ort_session = None
        self._ocr = None
        self._clahe = None   # pre-created once in on_start
        self._frame_counts: dict[str, int] = {}
        self._created_dirs: set[str] = set()
        # Per-source: whether the last processed frame contained vehicle detections.
        # Used to bypass skip_factor when vehicles are actively present.
        self._last_had_vehicles: dict[str, bool] = {}

        # Per-source active tracks: {source_id: {track_id: _TrackState}}
        self._tracks: dict[str, dict[int, _TrackState]] = {}
        self._track_counter: int = 0
        # Plate-level dedup: {(source_id, plate_text): last_saved_epoch}
        # Prevents saving the same plate multiple times within _PLATE_DEDUP_SECS.
        self._plate_last_saved: dict[tuple, float] = {}
        # NOT_DETECTED throttle: {(source_id, track_id): last_saved_epoch}
        self._not_detected_last_saved: dict[tuple, float] = {}


        # Background save queue — keeps disk I/O off the pyfunc hot path
        import queue, threading
        self._save_queue: queue.Queue = queue.Queue(maxsize=200)
        self._save_thread = threading.Thread(
            target=self._save_worker, daemon=True, name="plate-save"
        )
        self._save_thread.start()

        # Per-source OCR skip factor: process 1 out of every N frames.
        # cam_online_2 uses a smaller skip (more frequent OCR) to improve
        # motorcycle plate detection on that camera.
        self._skip_factors: dict[str, int] = {}
        self._default_skip_factor = 1

    # ------------------------------------------------------------------
    # Savant lifecycle hooks
    # ------------------------------------------------------------------

    def on_start(self) -> bool:
        """Called once before the pipeline starts processing frames."""
        if not super().on_start():
            return False
        try:
            self._init_models()
        except Exception as exc:
            logger.error("PlateOCR on_start failed: %s", exc, exc_info=True)
            return False
        self._reorganize_existing()
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reorganize_existing(self) -> None:
        """
        On startup, move any flat (unclassified) detect files into per-category
        subfolders.  Runs in the background so it doesn't delay pipeline start.
        """
        import threading
        threading.Thread(target=self._reorganize_worker, daemon=True).start()

    def _reorganize_worker(self) -> None:
        """Background worker: scan all source/date dirs and sort flat files."""
        if not os.path.isdir(_DETECT_BASE_DIR):
            return
        moved_total = 0
        for source in os.listdir(_DETECT_BASE_DIR):
            source_dir = os.path.join(_DETECT_BASE_DIR, source)
            if not os.path.isdir(source_dir):
                continue
            for date in os.listdir(source_dir):
                day_dir = os.path.join(source_dir, date)
                if not os.path.isdir(day_dir):
                    continue
                # Skip dirs that are themselves category folders
                if date in {cat for cat, _ in _PLATE_CATEGORIES} | {_CATEGORY_UNKNOWN}:
                    continue
                moved = self._reorganize_day(day_dir)
                if moved:
                    logger.info("Reorganized %d events in %s/%s", moved, source, date)
                    moved_total += moved
        if moved_total:
            logger.info("Startup reorganization complete: %d events moved", moved_total)

    def _save_worker(self) -> None:
        """Background thread: drains the save queue and writes files to disk."""
        import queue
        while True:
            try:
                task = self._save_queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                save_dir, prefix, vehicle_crop, plate_crop, event = task
                os.makedirs(save_dir, exist_ok=True)
                cv2.imwrite(os.path.join(save_dir, f"{prefix}_vehicle.jpg"), vehicle_crop)
                if plate_crop is not None:
                    cv2.imwrite(os.path.join(save_dir, f"{prefix}_plate.jpg"), plate_crop)
                with open(os.path.join(save_dir, f"{prefix}.json"), "w") as f:
                    json.dump(event, f, ensure_ascii=False, indent=2)
            except Exception as exc:
                logger.warning("Save worker error: %s", exc)
            finally:
                self._save_queue.task_done()

    def _reorganize_day(self, day_dir: str) -> int:
        """Move flat JSON+image files in day_dir into per-category subfolders."""
        try:
            entries = os.listdir(day_dir)
        except OSError:
            return 0

        json_files = [f for f in entries if f.endswith('.json') and os.path.isfile(os.path.join(day_dir, f))]
        if not json_files:
            return 0

        import shutil
        moved = 0
        for jf in json_files:
            json_path = os.path.join(day_dir, jf)
            try:
                with open(json_path) as fp:
                    event = json.load(fp)
            except Exception:
                continue

            had_category = "plate_category" in event
            if not had_category:
                plate_number = event.get("plate_number", "")
                normalized = _normalize_plate(plate_number)
                category = _classify_plate(normalized)
                event["plate_category"] = category
            else:
                category = event["plate_category"]

            dest_dir = os.path.join(day_dir, category)
            try:
                os.makedirs(dest_dir, exist_ok=True)
            except OSError as exc:
                logger.warning("Cannot create %s: %s", dest_dir, exc)
                continue

            prefix = jf[:-5]
            for fname in (jf, prefix + "_vehicle.jpg", prefix + "_plate.jpg"):
                src = os.path.join(day_dir, fname)
                dst = os.path.join(dest_dir, fname)
                if os.path.exists(src):
                    try:
                        shutil.move(src, dst)
                    except OSError as exc:
                        logger.warning("Cannot move %s: %s", fname, exc)

            if not had_category:
                try:
                    with open(os.path.join(dest_dir, jf), "w") as fp:
                        json.dump(event, fp, ensure_ascii=False, indent=2)
                except OSError:
                    pass

            moved += 1
        return moved

    def _init_models(self):
        import onnxruntime as ort
        from paddleocr import PaddleOCR

        os.makedirs(_DETECT_BASE_DIR, exist_ok=True)
        logger.info("Loading plate detection model: %s", self.plate_model_path)
        self._ort_session = ort.InferenceSession(
            self.plate_model_path,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        self._input_name = self._ort_session.get_inputs()[0].name
        active_provider = self._ort_session.get_providers()[0]
        logger.info("Plate detector using provider: %s", active_provider)

        # Warm up CUDA kernels so the first real frame does not trigger
        # 900ms+ compilation latency that fills the GStreamer queue and
        # causes the ZMQ ACK timeout / ingress crash loop.
        model_sz = self._ort_session.get_inputs()[0].shape[2]
        logger.info("Warming up plate detector (%dx%d) ...", model_sz, model_sz)
        dummy = np.zeros((1, 3, model_sz, model_sz), dtype=np.float32)
        for _ in range(5):
            self._ort_session.run(None, {self._input_name: dummy})
        logger.info("Plate detector warmup done.")

        logger.info("Initializing PaddleOCR (lang=en, GPU)...")
        self._ocr = PaddleOCR(
            use_angle_cls=True,   # classify rotated/tilted plates
            lang="en",
            use_gpu=True,
            show_log=False,
            rec_algorithm="SVTR_LCNet",
        )
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))

        # Warm up PaddleOCR GPU kernels — first real inference triggers CUDA
        # compilation (~30s) which causes ZMQ ACK timeout and ingress crash.
        logger.info("Warming up PaddleOCR ...")
        dummy_plate = np.zeros((64, 256, 3), dtype=np.uint8)
        self._ocr(dummy_plate)
        logger.info("PaddleOCR warmup done.")

        logger.info("PlateOCR models ready.")

    def _detect_plates(
        self, vehicle_bgr: np.ndarray
    ) -> list[tuple[int, int, int, int, float]]:
        """
        Detect plate bounding boxes inside a vehicle crop.

        Returns list of (x1, y1, x2, y2, confidence) in vehicle-crop pixels.
        """
        h, w = vehicle_bgr.shape[:2]

        # Preprocess to model input size (read from session, e.g. 640 or 1280)
        model_sz = self._ort_session.get_inputs()[0].shape[2]  # e.g. 640 or 1280
        resized = cv2.resize(vehicle_bgr, (model_sz, model_sz))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = rgb.transpose(2, 0, 1)[np.newaxis]  # [1, 3, model_sz, model_sz]

        raw = self._ort_session.run(None, {self._input_name: blob})[0]

        # YOLOv8 output shape: [1, 4+num_classes, num_anchors]
        # For a single-class plate model: [1, 5, anchors]
        preds = raw[0].T  # [anchors, 5]

        scores = preds[:, 4]
        mask = scores > self.plate_conf_threshold
        if not mask.any():
            return []

        preds = preds[mask]
        scores = scores[mask]

        # cx, cy, bw, bh in model_sz-space → x1,y1,x2,y2 in vehicle-crop pixels
        cx = preds[:, 0] / model_sz * w
        cy = preds[:, 1] / model_sz * h
        bw = preds[:, 2] / model_sz * w
        bh = preds[:, 3] / model_sz * h

        x1 = np.clip(cx - bw / 2, 0, w).astype(int)
        y1 = np.clip(cy - bh / 2, 0, h).astype(int)
        x2 = np.clip(cx + bw / 2, 0, w).astype(int)
        y2 = np.clip(cy + bh / 2, 0, h).astype(int)

        # Filter degenerate boxes
        valid = (x2 > x1) & (y2 > y1)
        if not valid.any():
            return []

        boxes_xyxy = np.stack([x1[valid], y1[valid], x2[valid], y2[valid]], axis=1)
        scores_valid = scores[valid]

        keep = _nms(boxes_xyxy.astype(float), scores_valid, self.nms_iou_threshold)
        return [
            (
                int(boxes_xyxy[i, 0]),
                int(boxes_xyxy[i, 1]),
                int(boxes_xyxy[i, 2]),
                int(boxes_xyxy[i, 3]),
                float(scores_valid[i]),
            )
            for i in keep
        ]

    def _preprocess_plate(self, plate_bgr: np.ndarray) -> np.ndarray:
        """
        Adaptive preprocessing for plate crops:
          1. Gamma correction: brightens dark (night) crops, leaves bright ones alone.
          2. CLAHE contrast enhancement.
          3. Unsharp-mask sharpening.
        Returns BGR image (same format as input).
        """
        brightness = _mean_brightness(plate_bgr)

        if brightness < _NIGHT_BRIGHTNESS_THRESH:
            # Dark / nighttime: brighten aggressively then normalise contrast
            # gamma < 1 → brightening curve; scale with darkness (darker = more boost)
            gamma = max(0.3, brightness / _NIGHT_BRIGHTNESS_THRESH * 0.6)
            plate_bgr = _gamma_correct(plate_bgr, gamma)

        gray = cv2.cvtColor(plate_bgr, cv2.COLOR_BGR2GRAY)
        gray = self._clahe.apply(gray)
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
        sharpened = cv2.filter2D(gray, -1, kernel)
        return cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)

    def _ocr_lines(self, img: np.ndarray) -> list[tuple[str, float]]:
        """
        Run PaddleOCR on img, return list of (text, conf) sorted top-to-bottom.
        Uses _LINE_DETECT_CONF (0.40) so dim top-lines on 2-line plates are kept.
        """
        result = self._ocr.ocr(img, cls=True)
        if not result or not result[0]:
            return []
        lines = sorted(result[0], key=lambda ln: ln[0][0][1])
        out = []
        for line in lines:
            _, (text, conf) = line[0], line[1]
            filtered = "".join(c for c in text.upper() if c in _PLATE_ALLOWED or c == 'O')
            if filtered and conf >= _LINE_DETECT_CONF:
                out.append((filtered, conf))
        return out

    def _run_ocr(self, plate_bgr: np.ndarray) -> tuple[Optional[str], float]:
        """
        Run PaddleOCR on a plate crop.

        Returns (plate_text, confidence), or (None, 0.0) if nothing found.

        Strategy:
        1. Upscale to at least 64×160 for reliable OCR.
        2. CLAHE + sharpen preprocessing.
        3. Full-image OCR with per-line threshold 0.40 (lower than final 0.60)
           so dim top-lines on 2-line motorcycle plates are not dropped.
        4. If result is digit-only (top line missed), split plate at mid-height
           and OCR the top half separately as fallback.
        5. Final average confidence must reach ocr_conf_threshold (0.60).
        """
        if plate_bgr.size == 0:
            return None, 0.0

        # Upscale to ensure OCR-friendly resolution
        ph, pw = plate_bgr.shape[:2]
        if ph < 64 or pw < 160:
            scale = max(64.0 / ph, 160.0 / pw)
            plate_bgr = cv2.resize(
                plate_bgr, (int(pw * scale), int(ph * scale)),
                interpolation=cv2.INTER_CUBIC,
            )

        plate_bgr = self._preprocess_plate(plate_bgr)

        valid_lines = self._ocr_lines(plate_bgr)
        if not valid_lines:
            return None, 0.0

        # Two-line plate: join top + bottom; one-line: use as-is
        plate_text = "-".join(t for t, _ in valid_lines[:2])
        avg_conf   = sum(c for _, c in valid_lines[:2]) / len(valid_lines[:2])

        # Fallback: if we only got digits (top line missed), try split-line OCR
        if plate_text.replace("-", "").isdigit():
            fallback_text, fallback_conf = self._run_ocr_split(plate_bgr, plate_text, avg_conf)
            if fallback_text:
                plate_text, avg_conf = fallback_text, fallback_conf

        if avg_conf < self.ocr_conf_threshold:
            return None, 0.0

        # Reject results that are clearly too short or too long for a VN plate
        stripped_len = len(re.sub(r'[-.\s]', '', plate_text))
        if stripped_len < 5 or stripped_len > 9:
            return None, 0.0

        return plate_text, avg_conf

    def _run_ocr_split(
        self, plate_bgr: np.ndarray, digit_text: str, digit_conf: float
    ) -> tuple[Optional[str], float]:
        """
        Split plate at mid-height and OCR the top half separately.
        Used as fallback when full-image OCR only returns the digit line.
        """
        ph = plate_bgr.shape[0]
        top_half = plate_bgr[: ph // 2, :]

        # Ensure top half is tall enough for OCR
        th = top_half.shape[0]
        if th < 24:
            return None, 0.0
        if th < 48:
            scale = 48.0 / th
            top_half = cv2.resize(
                top_half,
                (int(top_half.shape[1] * scale), 48),
                interpolation=cv2.INTER_CUBIC,
            )

        top_lines = self._ocr_lines(top_half)
        if not top_lines:
            return None, 0.0

        # Pick the highest-confidence non-empty line from the top half
        top_text, top_conf = max(top_lines, key=lambda x: x[1])

        # Only useful if the top half actually contains letters (region/series)
        if top_text.replace("-", "").isdigit():
            return None, 0.0

        combined  = f"{top_text}-{digit_text}"
        avg_conf  = (top_conf + digit_conf) / 2
        return combined, avg_conf

    def _match_or_create_track(
        self, source_id: str, bbox: tuple, label: str, now: float,
        ds_track_id: Optional[int] = None,
    ) -> _TrackState:
        """
        Return _TrackState for this vehicle detection.

        If ds_track_id is a valid DeepStream tracker ID (not None and not the
        UNTRACKED sentinel value 0xFFFFFFFFFFFFFFFF), use it directly as key —
        the tracker guarantees cross-frame identity.

        Otherwise fall back to center-distance matching: find the nearest active
        track whose center is within 1.5× the bbox diagonal, or create a new one.
        """
        _UNTRACKED = 18446744073709551615  # DeepStream UNTRACKED_OBJECT_ID
        source_tracks = self._tracks.setdefault(source_id, {})

        if ds_track_id is not None and ds_track_id != _UNTRACKED:
            track = source_tracks.get(ds_track_id)
            if track is not None:
                track.bbox = bbox
                track.last_seen = now
                return track
            new_track = _TrackState(ds_track_id, bbox, label, now)
            source_tracks[ds_track_id] = new_track
            return new_track

        # No tracker — use center-distance matching
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        bw = bbox[2] - bbox[0]
        bh = bbox[3] - bbox[1]
        max_dist = 1.5 * ((bw ** 2 + bh ** 2) ** 0.5)

        best_track = None
        best_dist = float("inf")
        for t in source_tracks.values():
            tcx = (t.bbox[0] + t.bbox[2]) / 2
            tcy = (t.bbox[1] + t.bbox[3]) / 2
            dist = ((cx - tcx) ** 2 + (cy - tcy) ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_track = t

        if best_track is not None and best_dist <= max_dist:
            best_track.bbox = bbox
            best_track.last_seen = now
            return best_track

        self._track_counter += 1
        new_track = _TrackState(self._track_counter, bbox, label, now)
        source_tracks[self._track_counter] = new_track
        return new_track

    def _flush_expired_tracks(self, source_id: str, now: float) -> None:
        """
        Remove tracks that have not been seen for _TRACK_MAX_AGE seconds.
        For each expired track that has a buffered best result, enqueue a save.
        """
        source_tracks = self._tracks.get(source_id)
        if not source_tracks:
            return

        expired_ids = [tid for tid, t in source_tracks.items() if now - t.last_seen > _TRACK_MAX_AGE]
        expired = [source_tracks.pop(tid) for tid in expired_ids]

        for t in expired:
            if t.best_plate is None:
                continue

            # Skip if the same plate was saved recently (dedup within _PLATE_DEDUP_SECS)
            dedup_key = (source_id, t.best_plate)
            last_saved = self._plate_last_saved.get(dedup_key, 0.0)
            if now - last_saved < _PLATE_DEDUP_SECS:
                logger.debug(
                    "Track %d plate='%s' skipped (dedup, last saved %.0fs ago)",
                    t.track_id, t.best_plate, now - last_saved,
                )
                continue
            self._plate_last_saved[dedup_key] = now

            plate_category = _classify_plate(t.best_plate)
            logger.debug(
                "Track %d expired: best='%s' conf=%.2f cat=%s",
                t.track_id, t.best_plate, t.best_ocr_conf, plate_category,
            )
            self._save_crops(
                source_id=source_id,
                label=t.label,
                plate_text=t.best_plate,
                vehicle_crop=t.best_vehicle_crop,
                plate_crop=t.best_plate_crop,
                vehicle_bbox=t.best_vehicle_bbox,
                plate_bbox=t.best_plate_bbox,
                ocr_conf=t.best_ocr_conf,
                plate_det_conf=t.best_det_conf,
                plate_category=plate_category,
            )

    def _save_crops(
        self,
        source_id: str,
        label: str,
        plate_text: str,
        vehicle_crop: np.ndarray,
        plate_crop: np.ndarray,
        vehicle_bbox: tuple[int, int, int, int],
        plate_bbox: tuple[int, int, int, int],
        ocr_conf: float,
        plate_det_conf: float,
        plate_category: str,
    ) -> None:
        """Enqueue vehicle/plate crops + JSON event for background disk write."""
        try:
            now = datetime.now(_VN_TZ)
            date_str = now.strftime("%Y-%m-%d")
            ts = now.strftime("%H%M%S_%f")[:10]  # HHMMSSmmm

            save_dir = os.path.join(_DETECT_BASE_DIR, source_id, date_str, plate_category)
            safe_plate = plate_text.replace("-", "_")
            prefix = f"{ts}_{label}_{safe_plate}"

            vx1, vy1, vx2, vy2 = vehicle_bbox
            px1, py1, px2, py2 = plate_bbox
            event = {
                "timestamp": now.isoformat(),
                "source_id": source_id,
                "label": label,
                "plate_number": plate_text,
                "plate_category": plate_category,
                "ocr_confidence": round(ocr_conf, 4),
                "plate_det_confidence": round(plate_det_conf, 4),
                "vehicle_bbox": {"x1": vx1, "y1": vy1, "x2": vx2, "y2": vy2},
                "plate_bbox_in_vehicle": {"x1": px1, "y1": py1, "x2": px2, "y2": py2},
                "files": {
                    "vehicle": f"{prefix}_vehicle.jpg",
                    "plate": f"{prefix}_plate.jpg",
                },
            }
            # Crops are already owned copies (from _TrackState.update_best)
            self._save_queue.put_nowait(
                (save_dir, prefix, vehicle_crop, plate_crop, event)
            )
        except Exception as exc:
            logger.warning("Failed to enqueue save task: %s", exc)

    def _save_not_detected(
        self,
        source_id: str,
        label: str,
        vehicle_crop: np.ndarray,
        vehicle_bbox: tuple,
        track_id: int,
        now: float,
    ) -> None:
        """
        Save vehicle crops where plate detection failed, for later annotation.
        Throttled to _NOT_DETECTED_INTERVAL_SECS per track to avoid flooding.
        """
        nd_key = (source_id, track_id)
        if now - self._not_detected_last_saved.get(nd_key, 0.0) < _NOT_DETECTED_INTERVAL_SECS:
            return
        self._not_detected_last_saved[nd_key] = now

        try:
            today = datetime.now(_VN_TZ).strftime("%Y-%m-%d")
            ts = datetime.now(_VN_TZ).strftime("%H%M%S_%f")[:10]
            save_dir = os.path.join(_DETECT_BASE_DIR, source_id, today, _NOT_DETECTED_DIR)
            prefix = f"{ts}_{label}_nd{track_id}"
            vx1, vy1, vx2, vy2 = vehicle_bbox
            event = {
                "timestamp": datetime.now(_VN_TZ).isoformat(),
                "source_id": source_id,
                "label": label,
                "plate_number": None,
                "vehicle_bbox": {"x1": vx1, "y1": vy1, "x2": vx2, "y2": vy2},
                "files": {"vehicle": f"{prefix}_vehicle.jpg"},
            }
            self._save_queue.put_nowait(
                (save_dir, prefix, vehicle_crop, None, event)
            )
        except Exception as exc:
            logger.warning("Failed to enqueue NOT_DETECTED save: %s", exc)

    # ------------------------------------------------------------------
    # Savant frame processing
    # ------------------------------------------------------------------

    def process_frame(self, buffer, frame_meta: NvDsFrameMeta) -> None:
        """Called for every frame; detects plates and attaches plate_number attributes."""
        import pyds

        # Ensure today's output directory exists (Vietnam time)
        today = datetime.now(_VN_TZ).strftime("%Y-%m-%d")
        day_dir = os.path.join(_DETECT_BASE_DIR, frame_meta.source_id, today)
        if day_dir not in self._created_dirs:
            os.makedirs(day_dir, exist_ok=True)
            self._created_dirs.add(day_dir)
            logger.info("Created output directory: %s", day_dir)

        # Run LPR every N frames normally, but process every frame when
        # vehicles were detected in the previous frame to avoid missing plates.
        source_id = frame_meta.source_id
        self._frame_counts[source_id] = self._frame_counts.get(source_id, 0) + 1
        skip = self._skip_factors.get(source_id, self._default_skip_factor)
        had_vehicles = self._last_had_vehicles.get(source_id, False)
        if not had_vehicles and self._frame_counts[source_id] % skip != 0:
            return

        # Get raw frame pixels (RGBA, HWC) from GStreamer buffer
        try:
            n_frame = pyds.get_nvds_buf_surface(hash(buffer), frame_meta.batch_id)
            frame_rgba = np.array(n_frame, copy=True, order="C")
        except Exception as exc:
            logger.warning("Cannot read frame buffer: %s", exc)
            return

        frame_bgr = cv2.cvtColor(frame_rgba, cv2.COLOR_RGBA2BGR)
        fh, fw = frame_bgr.shape[:2]

        roi = self._roi_zones.get(source_id)
        now = time.monotonic()
        self._flush_expired_tracks(source_id, now)

        vehicle_objects = [
            o for o in frame_meta.objects
            if not o.is_primary and o.label in _VEHICLE_LABELS
        ]
        self._last_had_vehicles[source_id] = len(vehicle_objects) > 0

        for obj_meta in vehicle_objects:
            # Bbox coords are in pixels (converter scales to frame space)
            bbox = obj_meta.bbox
            x1 = int(max(0, bbox.left))
            y1 = int(max(0, bbox.top))
            x2 = int(min(fw, bbox.left + bbox.width))
            y2 = int(min(fh, bbox.top + bbox.height))

            if x2 <= x1 or y2 <= y1:
                continue

            # Skip vehicles whose center is outside the configured ROI zone
            if roi is not None:
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                if not (roi[0] <= cx <= roi[2] and roi[1] <= cy <= roi[3]):
                    continue

            vehicle_crop = frame_bgr[y1:y2, x1:x2]
            plates = self._detect_plates(vehicle_crop)
            if not plates:
                # Only collect crops large enough to realistically contain a plate
                if (x2 - x1) >= 80:
                    track = self._match_or_create_track(
                        source_id, (x1, y1, x2, y2), obj_meta.label, now,
                        ds_track_id=obj_meta.track_id,
                    )
                    self._save_not_detected(
                        source_id=source_id,
                        label=obj_meta.label,
                        vehicle_crop=vehicle_crop.copy(),
                        vehicle_bbox=(x1, y1, x2, y2),
                        track_id=track.track_id,
                        now=now,
                    )
                continue

            # Take the highest-confidence plate detection
            plates.sort(key=lambda p: p[4], reverse=True)
            px1, py1, px2, py2, plate_det_conf = plates[0]
            plate_crop = vehicle_crop[py1:py2, px1:px2]

            # Use DeepStream track_id if tracker is active, else center-distance match
            track = self._match_or_create_track(
                source_id, (x1, y1, x2, y2), obj_meta.label, now,
                ds_track_id=obj_meta.track_id,
            )

            # Skip OCR on blurry frames — track stays alive for the next sharp frame
            if _sharpness(plate_crop) < _MIN_PLATE_SHARPNESS:
                continue

            plate_text, ocr_conf = self._run_ocr(plate_crop)
            if plate_text:
                plate_text = _normalize_plate(plate_text)
            if plate_text and ocr_conf >= self.ocr_conf_threshold:
                plate_category = _classify_plate(plate_text)
                try:
                    obj_meta.add_attr_meta(
                        element_name=_ATTR_ELEMENT,
                        name=_ATTR_NAME,
                        value=plate_text,
                        confidence=ocr_conf,
                    )
                    logger.debug(
                        "LPR [%s] plate='%s' category=%s ocr_conf=%.2f det_conf=%.2f",
                        obj_meta.label,
                        plate_text,
                        plate_category,
                        ocr_conf,
                        plate_det_conf,
                    )
                except Exception as exc:
                    logger.warning("add_attr_meta failed: %s", exc)

                # Buffer best result — save happens once when track expires
                track.update_best(
                    plate=plate_text,
                    plate_category=plate_category,
                    ocr_conf=ocr_conf,
                    det_conf=plate_det_conf,
                    vehicle_crop=vehicle_crop,
                    plate_crop=plate_crop,
                    vehicle_bbox=(x1, y1, x2, y2),
                    plate_bbox=(px1, py1, px2, py2),
                )
