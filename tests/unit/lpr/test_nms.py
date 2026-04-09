"""
Unit tests for _nms() (Non-Maximum Suppression) in src/lpr/plate_ocr.py

Validates IoU-based box filtering for plate detection post-processing.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import numpy as np
import pytest
from src.lpr.plate_ocr import _nms


class TestNMS:

    def test_empty_input_returns_empty(self):
        """No boxes → empty list."""
        result = _nms(np.zeros((0, 4)), np.zeros(0), 0.45)
        assert result == []

    def test_single_box_always_kept(self):
        """One box → always returned."""
        boxes = np.array([[0, 0, 100, 100]], dtype=float)
        scores = np.array([0.9])
        result = _nms(boxes, scores, 0.45)
        assert result == [0]

    def test_non_overlapping_boxes_all_kept(self):
        """Two boxes with IoU=0 → both kept."""
        boxes = np.array([
            [0,   0, 50,  50],    # top-left region
            [100, 100, 200, 200], # bottom-right region (no overlap)
        ], dtype=float)
        scores = np.array([0.9, 0.8])
        result = _nms(boxes, scores, 0.45)
        assert len(result) == 2
        assert 0 in result
        assert 1 in result

    def test_fully_overlapping_box_suppressed(self):
        """Identical boxes → only the highest-score one kept."""
        boxes = np.array([
            [0, 0, 100, 100],
            [0, 0, 100, 100],
        ], dtype=float)
        scores = np.array([0.9, 0.7])
        result = _nms(boxes, scores, 0.45)
        assert len(result) == 1
        assert result[0] == 0  # highest score wins

    def test_high_iou_suppresses_low_score(self):
        """High IoU (>threshold) → only highest score box kept."""
        boxes = np.array([
            [0,  0, 100, 100],
            [5,  5, 95,  95],   # high overlap with box 0
        ], dtype=float)
        scores = np.array([0.95, 0.85])
        result = _nms(boxes, scores, 0.45)
        assert len(result) == 1
        assert result[0] == 0

    def test_low_iou_keeps_both(self):
        """IoU below threshold → both boxes kept."""
        boxes = np.array([
            [0,   0,  60,  60],
            [55, 55, 120, 120],  # small overlap
        ], dtype=float)
        scores = np.array([0.9, 0.8])
        result = _nms(boxes, scores, 0.45)
        assert len(result) == 2

    def test_higher_threshold_keeps_more_boxes(self):
        """With IoU threshold=0.9, boxes with moderate overlap should both survive."""
        boxes = np.array([
            [0,  0, 100, 100],
            [10, 10, 90,  90],   # moderate overlap
        ], dtype=float)
        scores = np.array([0.9, 0.85])
        # Low threshold → only 1 kept
        result_strict = _nms(boxes, scores, 0.10)
        # High threshold → both kept
        result_loose  = _nms(boxes, scores, 0.95)
        assert len(result_strict) < len(result_loose)

    def test_sorted_by_score_not_input_order(self):
        """First kept box must be highest score regardless of input order."""
        boxes = np.array([
            [0, 0, 100, 100],
            [0, 0, 100, 100],
            [0, 0, 100, 100],
        ], dtype=float)
        scores = np.array([0.5, 0.9, 0.7])
        result = _nms(boxes, scores, 0.5)
        assert result[0] == 1  # index 1 has highest score=0.9
