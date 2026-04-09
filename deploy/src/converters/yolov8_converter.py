import numpy as np

from savant.base.converter import BaseComplexModelOutputConverter
from savant.base.model import ObjectModel


class YOLOv8Converter(BaseComplexModelOutputConverter):
    """
    Converter for YOLOv8-style Tensor outputs used by Savant nvinfer complex_model.

    It converts raw output0 tensor into:
      - bbox tensor: (class_id, confidence, xc, yc, w, h)
      - attr tensor: empty (no extra attributes at this stage)
    """

    def __init__(self, confidence_threshold: float = 0.25, nms_iou_threshold: float = 0.45):
        self.conf_threshold = confidence_threshold
        self.nms_iou_threshold = nms_iou_threshold

    def __call__(self, *output_layers, model: ObjectModel, roi):
        # Savant passes the main tensor as the first layer.
        output = output_layers[0]

        # Typical shape: [..., C, A] or [1, 84, 8400]. We normalize to [A, C].
        # vms-savant assumes output can be reshaped to [84, num_anchors].
        output = output.reshape(output.shape[-2], output.shape[-1])
        output = output.T  # [num_anchors, 84]

        boxes_cx_cy_w_h = output[:, :4]
        class_scores = output[:, 4:]

        class_ids = np.argmax(class_scores, axis=1)
        confidences = class_scores[np.arange(len(class_scores)), class_ids]

        mask = confidences > self.conf_threshold
        boxes = boxes_cx_cy_w_h[mask]
        class_ids = class_ids[mask]
        confidences = confidences[mask]

        n = len(boxes)
        if n == 0:
            return (
                np.empty((0, 6), dtype=np.float32),
                np.empty((0, 0), dtype=np.float32),
            )

        cx, cy, w, h = boxes.T

        # roi = (top, left, width, height)
        roi_top, roi_left, roi_width, roi_height = roi

        # Scale from model input (640x640) to roi-relative coordinates, then offset by roi origin.
        cx_scaled = cx / 640.0 * roi_width + roi_left
        cy_scaled = cy / 640.0 * roi_height + roi_top
        w_scaled = w / 640.0 * roi_width
        h_scaled = h / 640.0 * roi_height

        bbox_tensor = np.stack(
            [
                class_ids.astype(np.float32),
                confidences.astype(np.float32),
                cx_scaled.astype(np.float32),
                cy_scaled.astype(np.float32),
                w_scaled.astype(np.float32),
                h_scaled.astype(np.float32),
            ],
            axis=1,
        )

        attr_tensor = np.empty((n, 0), dtype=np.float32)
        return bbox_tensor, attr_tensor

