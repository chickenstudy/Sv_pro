"""sGIE output converter cho YOLOv8-face.

Model: derronqi/yolov8-face (1, 3, 640, 640) float32, RGB normalized [0,1].
Output: 3 feature maps (strides 8/16/32), shape (1, 80, H, W).
        Channels 80 = 64 (DFL bbox 4x16) + 1 (face cls) + 15 (5 kps x (xy+score)).

Chạy như nvinfer@complex_model dạng sGIE: input là crop của object parent (person).

Return type theo BaseComplexModelOutputConverter:
    Tuple[np.ndarray, List[List[Tuple[str, Any, float]]]]
  - bbox_tensor  np.ndarray (N, 6): (class_id, confidence, cx, cy, w, h)
  - values       List[List[(attr_name, value, confidence)]]: outer per-bbox,
                 inner per-attribute. Ở đây mỗi face có duy nhất attribute
                 "landmarks" = list 10 float (5 keypoints xy).

Trong module.yml cấu hình attribute:
  output:
    attributes:
      - name: landmarks
"""

from __future__ import annotations

import numpy as np
from savant.base.converter import BaseComplexModelOutputConverter
from savant.base.model import ObjectModel


class YOLOv8FaceConverter(BaseComplexModelOutputConverter):
    """sGIE converter cho YOLOv8-face: decode DFL + 5-pt landmarks."""

    INPUT_SIZE = 640
    REG_MAX    = 16
    NUM_KPS    = 5
    STRIDES    = (8, 16, 32)

    def __init__(
        self,
        confidence_threshold: float = 0.55,
        nms_iou_threshold:    float = 0.40,
    ):
        self.conf_threshold    = float(confidence_threshold)
        self.nms_iou_threshold = float(nms_iou_threshold)
        self._dfl_project      = np.arange(self.REG_MAX, dtype=np.float32)
        # Cache anchors theo (h, w, stride) — feature map sizes cố định nên chỉ tính 1 lần
        self._anchor_cache: dict[tuple, np.ndarray] = {}

    # ──────────────────────────────────────────────────────────────────────
    # Savant interface
    # ──────────────────────────────────────────────────────────────────────

    def __call__(self, *output_layers, model: ObjectModel, roi):
        """
        output_layers : tuple[ndarray] — 3 tensor (1, 80, H, W) theo thứ tự stride 8/16/32
                       Thứ tự ứng với thứ tự layer_names trong module.yml.
        roi           : (top, left, width, height) — crop bbox của parent object.
        """
        if len(output_layers) != 3:
            return self._empty()

        all_boxes:  list[np.ndarray] = []
        all_scores: list[np.ndarray] = []
        all_kps:    list[np.ndarray] = []

        for out, stride in zip(output_layers, self.STRIDES):
            # out.shape = (1, 80, H, W) → (H*W, 80)
            if out.ndim == 4:
                _, c, h, w = out.shape
            elif out.ndim == 3:
                c, h, w = out.shape
            else:
                continue
            feat = out.reshape(c, h * w).T   # (N, 80)

            # class score
            scores = self._sigmoid(feat[:, 64])                       # (N,)
            keep   = scores >= self.conf_threshold
            if not keep.any():
                continue

            sc      = scores[keep]
            box_dfl = feat[keep, 0:64].reshape(-1, 4, self.REG_MAX)   # (M, 4, 16)
            kps_raw = feat[keep, 65:80].reshape(-1, self.NUM_KPS, 3)  # (M, 5, 3)

            # DFL → distance
            distances  = (self._softmax(box_dfl, axis=-1) * self._dfl_project).sum(axis=-1) * stride

            anchors = self._get_anchors(h, w, stride)[keep]           # (M, 2)

            x1 = anchors[:, 0] - distances[:, 0]
            y1 = anchors[:, 1] - distances[:, 1]
            x2 = anchors[:, 0] + distances[:, 2]
            y2 = anchors[:, 1] + distances[:, 3]
            boxes = np.stack([x1, y1, x2, y2], axis=-1)               # (M, 4)

            # Keypoints: (x_raw * 2) * stride + anchor
            kp_xy = kps_raw[:, :, :2] * 2.0 * stride
            kp_xy[:, :, 0] += anchors[:, None, 0]
            kp_xy[:, :, 1] += anchors[:, None, 1]

            all_boxes.append(boxes)
            all_scores.append(sc)
            all_kps.append(kp_xy)

        if not all_boxes:
            return self._empty()

        boxes_arr  = np.concatenate(all_boxes, axis=0)
        scores_arr = np.concatenate(all_scores, axis=0)
        kps_arr    = np.concatenate(all_kps, axis=0)

        keep = self._nms(boxes_arr, scores_arr, self.nms_iou_threshold)
        if not keep:
            return self._empty()

        boxes_arr  = boxes_arr[keep]
        scores_arr = scores_arr[keep]
        kps_arr    = kps_arr[keep]

        # ── Scale từ model input space (640) sang roi-pixel space ─────────
        roi_top, roi_left, roi_width, roi_height = roi
        sx = float(roi_width)  / self.INPUT_SIZE
        sy = float(roi_height) / self.INPUT_SIZE

        x1 = boxes_arr[:, 0] * sx + roi_left
        y1 = boxes_arr[:, 1] * sy + roi_top
        x2 = boxes_arr[:, 2] * sx + roi_left
        y2 = boxes_arr[:, 3] * sy + roi_top

        cx = (x1 + x2) * 0.5
        cy = (y1 + y2) * 0.5
        bw = (x2 - x1)
        bh = (y2 - y1)

        # Keypoints theo cùng scale
        kps_arr[:, :, 0] = kps_arr[:, :, 0] * sx + roi_left
        kps_arr[:, :, 1] = kps_arr[:, :, 1] * sy + roi_top

        n = len(scores_arr)
        bbox_tensor = np.stack([
            np.zeros(n, dtype=np.float32),            # class_id 0 = face
            scores_arr.astype(np.float32),
            cx.astype(np.float32),
            cy.astype(np.float32),
            bw.astype(np.float32),
            bh.astype(np.float32),
        ], axis=1)

        # Savant kỳ vọng values là List[List[Tuple[name, value, confidence]]].
        # Trả ndarray (N, 10) sẽ làm `if values:` trong NvInferProcessor ném
        # ValueError "ambiguous truth value" → pad chết, pipeline crash.
        kps_flat = kps_arr.reshape(n, self.NUM_KPS * 2).astype(np.float32)
        values = [
            [("landmarks", kps_flat[i].tolist(), float(scores_arr[i]))]
            for i in range(n)
        ]

        return bbox_tensor, values

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    def _empty(self):
        return (
            np.empty((0, 6), dtype=np.float32),
            [],
        )

    def _get_anchors(self, h: int, w: int, stride: int) -> np.ndarray:
        key = (h, w, stride)
        cached = self._anchor_cache.get(key)
        if cached is not None:
            return cached
        ys, xs = np.mgrid[:h, :w]
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
        e_x   = np.exp(x - x_max)
        return e_x / e_x.sum(axis=axis, keepdims=True)

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thresh: float) -> list[int]:
        if len(boxes) == 0:
            return []
        x1 = boxes[:, 0]; y1 = boxes[:, 1]
        x2 = boxes[:, 2]; y2 = boxes[:, 3]
        areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
        order = scores.argsort()[::-1]

        keep: list[int] = []
        while order.size > 0:
            i = int(order[0])
            keep.append(i)
            if order.size == 1:
                break
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            iou   = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
            order = order[1:][iou <= iou_thresh]
        return keep
