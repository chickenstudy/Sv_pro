"""
YOLOv8-Face Detector — Drop-in replacement cho SCRFD trong pipeline FR.

Model: derronqi/yolov8-face (mirror hpc203/yolov8-face-landmarks-opencv-dnn).
Input : (1, 3, 640, 640) float32, RGB normalized [0, 1], letterboxed.
Output: 3 feature maps (strides 8 / 16 / 32), shape (1, 80, H, W).
        Channels 80 = 64 (DFL bbox 4×16) + 1 (face cls) + 15 (5 kps × xy + score).

So với SCRFD-10G_bnkps (cùng API output):
  - YOLOv8n-face nhỏ hơn (12 MB vs 17 MB), inference nhanh hơn ~30%.
  - Confidence calibration ổn định hơn → ít false-positive trên non-face
    (tay, tường, mannequin pattern).
  - Output 5 keypoints chuẩn insightface (eyes, nose, mouth corners) →
    dùng trực tiếp cho ArcFace alignment, không cần convert.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
import onnxruntime as ort

logger = logging.getLogger(__name__)


class YOLOv8FaceDetector:
    """Face detector dùng YOLOv8-face ONNX, output API tương đương SCRFD."""

    def __init__(
        self,
        model_path: str,
        providers: list | None = None,
        input_size: int = 640,
        conf_thresh: float = 0.55,
        nms_thresh:  float = 0.40,
        reg_max:     int   = 16,    # DFL projection bins
    ):
        self.model_path  = model_path
        self.input_size  = input_size
        self.conf_thresh = conf_thresh
        self.nms_thresh  = nms_thresh
        self.reg_max     = reg_max

        providers = providers or ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(model_path, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]

        # DFL projection vector (0, 1, ..., reg_max-1) — tính 1 lần
        self._dfl_project = np.arange(reg_max, dtype=np.float32)

        # Cache anchor centers per stride per (H, W) — tránh recompute mỗi frame
        self._anchor_cache: dict[tuple, np.ndarray] = {}

        logger.info("YOLOv8-Face loaded: %s | provider=%s | input=%d×%d",
                    model_path, self.session.get_providers()[0],
                    input_size, input_size)

    # ──────────────────────────────────────────────────────────────────────
    # Public API — drop-in cho SCRFD._detect_faces(frame_bgr)
    # ──────────────────────────────────────────────────────────────────────

    def detect(
        self, frame_bgr: np.ndarray
    ) -> list[tuple[tuple[int, int, int, int], float, np.ndarray | None]]:
        """
        Detect face trong frame BGR.

        Trả về list[(bbox, score, kps_5pt)] với:
          - bbox  : (x1, y1, x2, y2) int trên không gian frame gốc
          - score : confidence ∈ [0, 1]
          - kps   : np.ndarray shape (5, 2) — toạ độ float trên frame gốc
                    Thứ tự: left_eye, right_eye, nose, mouth_left, mouth_right
        """
        if frame_bgr is None or frame_bgr.size == 0:
            return []

        orig_h, orig_w = frame_bgr.shape[:2]

        # ── Letterbox resize → 640×640 ────────────────────────────────────
        inp, scale, pad_x, pad_y = self._letterbox(frame_bgr, self.input_size)
        # BGR → RGB, HWC → CHW, [0,255] → [0,1], add batch dim
        inp = cv2.cvtColor(inp, cv2.COLOR_BGR2RGB)
        inp = inp.astype(np.float32) * (1.0 / 255.0)
        inp = inp.transpose(2, 0, 1)[np.newaxis]   # (1, 3, 640, 640)

        # ── Inference ─────────────────────────────────────────────────────
        outputs = self.session.run(self.output_names, {self.input_name: inp})

        # ── Decode mỗi stride ─────────────────────────────────────────────
        strides = (8, 16, 32)
        all_boxes:  list[np.ndarray] = []
        all_scores: list[np.ndarray] = []
        all_kps:    list[np.ndarray] = []

        for out, stride in zip(outputs, strides):
            # out shape (1, 80, H, W) → (H*W, 80)
            _, c, h, w = out.shape
            feat = out.reshape(c, h * w).T   # (H*W, C=80)

            # ── Class score ───────────────────────────────────────────────
            cls_logits = feat[:, 64:65]                      # (N, 1)
            scores     = self._sigmoid(cls_logits[:, 0])     # (N,)
            keep_mask  = scores >= self.conf_thresh
            if not keep_mask.any():
                continue

            sc       = scores[keep_mask]
            box_dfl  = feat[keep_mask, 0:64].reshape(-1, 4, self.reg_max)   # (M, 4, 16)
            kps_raw  = feat[keep_mask, 65:80].reshape(-1, 5, 3)             # (M, 5, 3)

            # ── DFL → distance (l, t, r, b) đơn vị grid → ×stride ─────────
            # softmax dọc reg_max bins, rồi sum × project → expected value
            box_softmax = self._softmax(box_dfl, axis=-1)
            distances   = (box_softmax * self._dfl_project).sum(axis=-1)    # (M, 4)
            distances  *= stride

            # ── Anchor centers cho stride này ─────────────────────────────
            anchors = self._get_anchors(h, w, stride)                       # (H*W, 2)
            anchors = anchors[keep_mask]                                    # (M, 2)

            # distance2bbox: (cx - left, cy - top, cx + right, cy + bottom)
            x1 = anchors[:, 0] - distances[:, 0]
            y1 = anchors[:, 1] - distances[:, 1]
            x2 = anchors[:, 0] + distances[:, 2]
            y2 = anchors[:, 1] + distances[:, 3]
            boxes = np.stack([x1, y1, x2, y2], axis=-1)                     # (M, 4)

            # ── Keypoints: kps_raw[:, :, :2] tính theo anchor + stride ────
            # Format derronqi: (x_offset, y_offset, score) raw, decode:
            #   kp_x = (raw_x * 2) * stride + anchor_x
            #   kp_y = (raw_y * 2) * stride + anchor_y
            # (giống YOLOv5/v8 keypoint head — sigmoid+scale+offset)
            kp_xy = kps_raw[:, :, :2] * 2.0 * stride                        # (M, 5, 2)
            kp_xy[:, :, 0] += anchors[:, np.newaxis, 0]
            kp_xy[:, :, 1] += anchors[:, np.newaxis, 1]
            # kp_score = self._sigmoid(kps_raw[:, :, 2])  — không dùng cho align

            all_boxes.append(boxes)
            all_scores.append(sc)
            all_kps.append(kp_xy)

        if not all_boxes:
            return []

        boxes_arr  = np.concatenate(all_boxes, axis=0)
        scores_arr = np.concatenate(all_scores, axis=0)
        kps_arr    = np.concatenate(all_kps, axis=0)

        # ── Reverse letterbox: model space → original frame space ─────────
        boxes_arr[:, [0, 2]] = (boxes_arr[:, [0, 2]] - pad_x) / scale
        boxes_arr[:, [1, 3]] = (boxes_arr[:, [1, 3]] - pad_y) / scale
        kps_arr[:, :, 0]     = (kps_arr[:, :, 0] - pad_x) / scale
        kps_arr[:, :, 1]     = (kps_arr[:, :, 1] - pad_y) / scale

        # Clip vào frame bounds
        boxes_arr[:, [0, 2]] = np.clip(boxes_arr[:, [0, 2]], 0, orig_w - 1)
        boxes_arr[:, [1, 3]] = np.clip(boxes_arr[:, [1, 3]], 0, orig_h - 1)

        # ── NMS ───────────────────────────────────────────────────────────
        keep = self._nms(boxes_arr, scores_arr, self.nms_thresh)

        results: list = []
        for k in keep:
            x1, y1, x2, y2 = boxes_arr[k]
            if x2 <= x1 or y2 <= y1:
                continue
            results.append((
                (int(x1), int(y1), int(x2), int(y2)),
                float(scores_arr[k]),
                kps_arr[k].astype(np.float32),
            ))
        return results

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _letterbox(img: np.ndarray, target: int):
        """
        Resize giữ aspect ratio + pad symmetric về target×target.
        Trả về (img_padded, scale, pad_x, pad_y) — pad_x/pad_y dùng để reverse.
        """
        h, w = img.shape[:2]
        scale = min(target / w, target / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)

        pad_x = (target - nw) // 2
        pad_y = (target - nh) // 2

        # Pad với màu xám 114 (chuẩn YOLOv5/v8)
        out = np.full((target, target, 3), 114, dtype=np.uint8)
        out[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
        return out, scale, pad_x, pad_y

    def _get_anchors(self, h: int, w: int, stride: int) -> np.ndarray:
        """Anchor centers (cx, cy) cho 1 feature map (H, W) với stride cho trước."""
        key = (h, w, stride)
        cached = self._anchor_cache.get(key)
        if cached is not None:
            return cached
        ys, xs = np.mgrid[:h, :w]
        # Trung tâm cell + 0.5 (anchor-free YOLOv8 convention)
        anchors = np.stack([xs + 0.5, ys + 0.5], axis=-1).astype(np.float32) * stride
        anchors = anchors.reshape(-1, 2)
        self._anchor_cache[key] = anchors
        return anchors

    @staticmethod
    def _sigmoid(x: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-x))

    @staticmethod
    def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
        x_max = x.max(axis=axis, keepdims=True)
        e_x = np.exp(x - x_max)
        return e_x / e_x.sum(axis=axis, keepdims=True)

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thresh: float) -> list[int]:
        """Greedy NMS, trả index list theo score giảm dần."""
        if len(boxes) == 0:
            return []
        x1 = boxes[:, 0]; y1 = boxes[:, 1]
        x2 = boxes[:, 2]; y2 = boxes[:, 3]
        areas = (x2 - x1) * (y2 - y1)
        order = scores.argsort()[::-1]

        keep: list[int] = []
        while order.size > 0:
            i = order[0]
            keep.append(int(i))
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
            inds = np.where(iou <= iou_thresh)[0]
            order = order[inds + 1]
        return keep
