"""
Bộ lọc chất lượng khuôn mặt (Face Quality Filter) cho pipeline FR của SV-PRO.

Đánh giá khuôn mặt trên 3 tiêu chí trước khi gửi sang ArcFace embedding:
  1. Độ nét (Sharpness): Laplacian variance phải > 50.
  2. Góc nghiêng (Pose): Yaw < 30°, Pitch < 25° (ước tính từ 5-point landmark).
  3. Độ sáng (Illumination): Mean brightness nằm trong khoảng [0.25, 0.95].

Trả về composite_score (float 0-1). Score < 0.50 → drop frame.
"""

import logging
import numpy as np
import cv2

logger = logging.getLogger(__name__)

# ── Ngưỡng chất lượng ──────────────────────────────────────────────────────────
_MIN_SHARPNESS = 110.0      # Laplacian variance tối thiểu (50 → 80 → 110, lọc thêm
                             #  ảnh tay/tường/mannequin có pattern mờ)
_MAX_YAW_DEG   = 35.0       # Góc xoay ngang tối đa
_MAX_PITCH_DEG = 30.0       # Góc cúi/ngẩng tối đa
_MIN_ILLUM     = 0.20       # Độ sáng tối thiểu (nới cho ban đêm)
_MAX_ILLUM     = 0.97       # Độ sáng tối đa
_MIN_COMPOSITE = 0.50       # Điểm tổng hợp tối thiểu (0.35 → 0.42 → 0.50, siết
                             #  thêm sau quan sát noise crop trên cam2/cam_online_1)


def compute_sharpness(face_bgr: np.ndarray) -> float:
    """
    Tính độ nét của ảnh khuôn mặt bằng phương sai Laplacian.
    Giá trị càng cao → ảnh càng rõ nét.
    """
    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def compute_illumination(face_bgr: np.ndarray) -> float:
    """
    Tính độ sáng trung bình của khuôn mặt, chuẩn hóa về khoảng [0, 1].
    Sử dụng kênh V trong không gian màu HSV.
    """
    hsv = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2HSV)
    mean_v = hsv[:, :, 2].mean() / 255.0
    return float(mean_v)


def estimate_pose_from_landmarks(landmarks_5pt: np.ndarray) -> tuple[float, float]:
    """
    Ước tính góc Yaw (xoay ngang) và Pitch (cúi/ngẩng) từ 5 điểm landmark.

    5-point landmark theo chuẩn InsightFace/SCRFD:
      [0] = mắt trái,  [1] = mắt phải
      [2] = mũi,       [3] = khóe miệng trái, [4] = khóe miệng phải

    Công thức ước tính đơn giản (không cần PnP solver):
      - Yaw:   Tính từ tỉ lệ khoảng cách mắt-mũi theo trục X.
      - Pitch: Tính từ vị trí mũi so với đường nối 2 mắt theo trục Y.

    Trả về (yaw_degrees, pitch_degrees). Giá trị âm = quay trái / cúi xuống.
    """
    if landmarks_5pt is None or len(landmarks_5pt) < 5:
        return 0.0, 0.0

    lm = landmarks_5pt.astype(np.float32)
    left_eye  = lm[0]
    right_eye = lm[1]
    nose      = lm[2]

    # Khoảng cách giữa 2 mắt (inter-ocular distance)
    iod = np.linalg.norm(right_eye - left_eye)
    if iod < 1e-6:
        return 0.0, 0.0

    # Midpoint (điểm giữa 2 mắt)
    eye_mid = (left_eye + right_eye) / 2.0

    # ── Yaw: mũi lệch trái/phải so với tâm mắt ────────────────────────────────
    # Giá trị dương = khuôn mặt quay sang phải
    yaw_ratio = (nose[0] - eye_mid[0]) / iod
    yaw_deg   = float(np.degrees(np.arctan(yaw_ratio * 2.0)))

    # ── Pitch: mũi cao/thấp hơn đường nối 2 mắt ──────────────────────────────
    # Giá trị dương = khuôn mặt ngẩng lên (theo tọa độ ảnh Y tăng xuống dưới)
    pitch_ratio = (nose[1] - eye_mid[1]) / iod
    pitch_deg   = float(np.degrees(np.arctan(pitch_ratio * 1.5)))

    return yaw_deg, pitch_deg


def compute_quality_score(
    face_bgr: np.ndarray,
    landmarks_5pt: np.ndarray | None = None,
) -> tuple[float, dict]:
    """
    Tính điểm chất lượng tổng hợp cho một crop khuôn mặt.

    Đầu vào:
      face_bgr     – Ảnh khuôn mặt BGR (đã crop+align 112x112 hoặc tương đương).
      landmarks_5pt – 5 điểm landmark dạng numpy array shape (5, 2), tùy chọn.

    Đầu ra:
      (composite_score, details_dict)
      - composite_score: float 0-1 (< _MIN_COMPOSITE → QC fail).
      - details_dict: {'sharpness', 'illumination', 'yaw', 'pitch', 'pass'}.
    """
    # ── Sharpness ──────────────────────────────────────────────────────────────
    sharpness = compute_sharpness(face_bgr)
    # Normalize: ngưỡng tối thiểu là _MIN_SHARPNESS (50.0), chia cho 100 để scale
    # Ảnh sharpness=50 → score=0.5, sharpness=100 → score=1.0
    sharp_score = min(sharpness / 100.0, 1.0)

    # ── Illumination ───────────────────────────────────────────────────────────
    illum = compute_illumination(face_bgr)
    # Score cao nhất khi nằm giữa khoảng [0.4, 0.7]
    if _MIN_ILLUM <= illum <= _MAX_ILLUM:
        illum_score = 1.0 - abs(illum - 0.55) / 0.45
    else:
        illum_score = 0.0

    # ── Pose ───────────────────────────────────────────────────────────────────
    yaw_deg, pitch_deg = estimate_pose_from_landmarks(landmarks_5pt) if landmarks_5pt is not None else (0.0, 0.0)
    yaw_score   = max(0.0, 1.0 - abs(yaw_deg)   / _MAX_YAW_DEG)
    pitch_score = max(0.0, 1.0 - abs(pitch_deg) / _MAX_PITCH_DEG)
    pose_score  = (yaw_score + pitch_score) / 2.0

    # ── Composite (trọng số): Sharpness 40%, Pose 35%, Illumination 25% ────────
    composite = (
        sharp_score * 0.40 +
        pose_score  * 0.35 +
        illum_score * 0.25
    )

    details = {
        "sharpness":  round(sharpness, 2),
        "sharp_score": round(sharp_score, 3),
        "illumination": round(illum, 3),
        "illum_score":  round(illum_score, 3),
        "yaw_deg":   round(yaw_deg, 2),
        "pitch_deg": round(pitch_deg, 2),
        "pose_score":  round(pose_score, 3),
        "composite":   round(composite, 3),
        "pass": composite >= _MIN_COMPOSITE,
    }
    return composite, details
