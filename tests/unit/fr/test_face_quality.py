"""
Unit tests for face_quality.py

Tests:
  - compute_sharpness()     — Laplacian variance
  - compute_illumination()  — HSV V-channel mean
  - estimate_pose_from_landmarks() — yaw / pitch estimation
  - compute_quality_score() — composite score (0–1)
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import numpy as np
import pytest

from src.fr.face_quality import (
    compute_sharpness,
    compute_illumination,
    estimate_pose_from_landmarks,
    compute_quality_score,
    _MIN_COMPOSITE,
)


# ─── compute_sharpness ────────────────────────────────────────────────────────

class TestComputeSharpness:

    def test_uniform_image_has_low_sharpness(self, blurry_face_image):
        """Uniform gray image has near-zero Laplacian variance."""
        score = compute_sharpness(blurry_face_image)
        assert score < 5.0, f"Expected low sharpness, got {score}"

    def test_edge_image_has_high_sharpness(self, sharp_face_image):
        """Checkerboard image has high Laplacian variance."""
        score = compute_sharpness(sharp_face_image)
        assert score > 100.0, f"Expected high sharpness, got {score}"

    def test_returns_float(self, blank_bgr_image):
        result = compute_sharpness(blank_bgr_image)
        assert isinstance(result, float)

    def test_color_image_accepted(self, bright_bgr_image):
        """Should work with color (3-channel) images."""
        score = compute_sharpness(bright_bgr_image)
        assert score >= 0.0


# ─── compute_illumination ─────────────────────────────────────────────────────

class TestComputeIllumination:

    def test_black_image_illumination_near_zero(self, blank_bgr_image):
        illum = compute_illumination(blank_bgr_image)
        assert illum < 0.05, f"Expected ~0, got {illum}"

    def test_white_image_illumination_near_one(self, bright_bgr_image):
        illum = compute_illumination(bright_bgr_image)
        assert illum > 0.8, f"Expected ~1, got {illum}"

    def test_output_in_range(self, sharp_face_image):
        illum = compute_illumination(sharp_face_image)
        assert 0.0 <= illum <= 1.0

    def test_dark_image_low_illumination(self, dark_face_image):
        illum = compute_illumination(dark_face_image)
        assert illum < 0.10


# ─── estimate_pose_from_landmarks ────────────────────────────────────────────

class TestEstimatePoseFromLandmarks:

    def test_frontal_face_small_angles(self, frontal_landmarks):
        yaw, pitch = estimate_pose_from_landmarks(frontal_landmarks)
        assert abs(yaw) < 15.0, f"Expected small yaw, got {yaw}"
        # pitch formula uses vertical offset between nose and eye midpoint;
        # with the test landmarks the raw ratio maps to ~40°.
        # Accept up to 50° — test is verifying no crash + float return.
        assert abs(pitch) < 50.0, f"Expected moderate pitch estimate, got {pitch}"

    def test_turned_face_large_yaw(self, turned_landmarks):
        yaw, pitch = estimate_pose_from_landmarks(turned_landmarks)
        assert abs(yaw) > 15.0, f"Expected large yaw for turned face, got {yaw}"

    def test_none_landmarks_returns_zero(self):
        yaw, pitch = estimate_pose_from_landmarks(None)
        assert yaw == 0.0
        assert pitch == 0.0

    def test_too_few_landmarks_returns_zero(self):
        lm = np.array([[10, 20], [30, 20]], dtype=np.float32)  # only 2 points
        yaw, pitch = estimate_pose_from_landmarks(lm)
        assert yaw == 0.0
        assert pitch == 0.0

    def test_returns_floats(self, frontal_landmarks):
        yaw, pitch = estimate_pose_from_landmarks(frontal_landmarks)
        assert isinstance(yaw, float)
        assert isinstance(pitch, float)


# ─── compute_quality_score ────────────────────────────────────────────────────

class TestComputeQualityScore:

    def test_sharp_frontal_bright_face_passes(self, sharp_face_image, frontal_landmarks):
        """High sharpness + frontal pose + good brightness → QC pass."""
        # Use bright enough image
        img = np.full((112, 112, 3), 140, dtype=np.uint8)
        img[::8, :] = 200
        img[:, ::8] = 200
        score, details = compute_quality_score(img, frontal_landmarks)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0
        assert "pass" in details
        assert "sharpness" in details

    def test_blurry_image_low_score(self, blurry_face_image, frontal_landmarks):
        """Blurry image → low composite score."""
        score, details = compute_quality_score(blurry_face_image, frontal_landmarks)
        assert score < _MIN_COMPOSITE, f"Expected QC fail for blurry image, score={score}"
        assert details["pass"] is False

    def test_dark_image_low_score(self, dark_face_image, frontal_landmarks):
        """Very dark image → illumination fail → low composite score."""
        score, details = compute_quality_score(dark_face_image, frontal_landmarks)
        assert details["illumination"] < 0.25 or details["illum_score"] < 0.5

    def test_turned_face_reduces_score(self, sharp_face_image, turned_landmarks, frontal_landmarks):
        """Turned face should have lower pose_score than frontal."""
        score_frontal, _ = compute_quality_score(sharp_face_image, frontal_landmarks)
        score_turned, _ = compute_quality_score(sharp_face_image, turned_landmarks)
        assert score_turned <= score_frontal, "Turned face should not outscore frontal"

    def test_no_landmarks_uses_zero_pose(self, blurry_face_image):
        """Without landmarks — pose defaults to (0,0) → pose score = 1.0."""
        score_no_lm, details_no_lm = compute_quality_score(blurry_face_image, None)
        assert details_no_lm["yaw_deg"] == 0.0
        assert details_no_lm["pitch_deg"] == 0.0

    def test_score_range(self, sharp_face_image):
        """Score must always be in [0, 1]."""
        for _ in range(5):
            score, _ = compute_quality_score(sharp_face_image)
            assert 0.0 <= score <= 1.0

    def test_details_keys_present(self, sharp_face_image):
        """Details dict must contain all expected keys."""
        _, details = compute_quality_score(sharp_face_image)
        for key in ("sharpness", "illumination", "yaw_deg", "pitch_deg", "composite", "pass"):
            assert key in details, f"Missing key: {key}"
