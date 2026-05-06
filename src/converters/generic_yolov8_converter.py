"""Generic YOLOv8 output converter for any number of classes.

Works for: fall_detector (3 cls), covered_person_detector (2 cls),
person_detector (1 cls), yolov8s (80 cls), etc.

Output tensor shape: [1, 4+num_classes, 8400]
"""
import numpy as np
from savant.base.converter import BaseComplexModelOutputConverter
from savant.base.model import ObjectModel


class GenericYOLOv8Converter(BaseComplexModelOutputConverter):
    def __init__(self, confidence_threshold=0.25, nms_iou_threshold=0.45):
        self.conf_threshold = confidence_threshold
        self.nms_iou_threshold = nms_iou_threshold

    def __call__(self, *output_layers, model: ObjectModel, roi):
        output = output_layers[0]
        # [1, 4+num_classes, 8400] → [8400, 4+num_classes]
        output = output.reshape(output.shape[-2], output.shape[-1]).T

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
        roi_top, roi_left, roi_width, roi_height = roi

        cx_scaled = cx / 640.0 * roi_width + roi_left
        cy_scaled = cy / 640.0 * roi_height + roi_top
        w_scaled = w / 640.0 * roi_width
        h_scaled = h / 640.0 * roi_height

        bbox_tensor = np.stack([
            class_ids.astype(np.float32),
            confidences.astype(np.float32),
            cx_scaled.astype(np.float32),
            cy_scaled.astype(np.float32),
            w_scaled.astype(np.float32),
            h_scaled.astype(np.float32),
        ], axis=1)

        attr_tensor = np.empty((n, 0), dtype=np.float32)
        return bbox_tensor, attr_tensor
