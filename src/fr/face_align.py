"""
Face alignment module — dùng chung cho FaceRecognizer (runtime) và
EnrollmentServer (operator upload).

CRITICAL: cả 2 phải dùng cùng template + cùng warpAffine settings.
Nếu không, embedding của cùng 1 người ở enroll vs runtime sẽ KHÁC NHAU
→ cosine similarity thấp → người quen bị nhận thành stranger.
"""

from __future__ import annotations

import numpy as np
import cv2

# ── Template insightface chuẩn — 5 landmark target trên canvas 112×112 ──────
# Thứ tự: left_eye, right_eye, nose, mouth_left, mouth_right
ARCFACE_DST = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float32)

ARCFACE_TARGET_SIZE = (112, 112)


def align_face(
    image_bgr: np.ndarray,
    landmarks_5pt: np.ndarray | None,
) -> np.ndarray:
    """
    Căn chỉnh khuôn mặt về 112×112 chuẩn ArcFace.

    - Có landmark 5-point → AffineTransform (estimateAffinePartial2D + warpAffine)
      → đây là cách CHÍNH XÁC mà ArcFace expect.
    - Không có landmark → fallback square-pad + resize (kém chính xác hơn,
      embedding sẽ lệch).

    Đầu vào:
      image_bgr: ảnh BGR — có thể là full frame HOẶC face crop.
                 Landmark phải ở CÙNG hệ tọa độ với ảnh này.
      landmarks_5pt: np.ndarray shape (5, 2) — toạ độ float trên image_bgr.

    Đầu ra: ảnh 112×112 BGR đã align.
    """
    if landmarks_5pt is not None and len(landmarks_5pt) == 5:
        M, _ = cv2.estimateAffinePartial2D(
            landmarks_5pt.astype(np.float32), ARCFACE_DST,
        )
        if M is not None:
            return cv2.warpAffine(
                image_bgr, M, ARCFACE_TARGET_SIZE,
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )

    # Fallback (kém chính xác — chỉ dùng khi thực sự không có landmark)
    return _square_resize(image_bgr, ARCFACE_TARGET_SIZE[0])


def _square_resize(img: np.ndarray, size: int) -> np.ndarray:
    """Pad ảnh thành square với mean color → resize về size×size."""
    h, w = img.shape[:2]
    side = max(h, w)
    if side == 0:
        return np.zeros((size, size, 3), dtype=np.uint8)
    if img.size > 0:
        mean = img.reshape(-1, img.shape[-1]).mean(axis=0).astype(np.uint8)
    else:
        mean = np.array([114, 114, 114], dtype=np.uint8)
    canvas = np.full((side, side, img.shape[-1]), mean, dtype=np.uint8)
    ox = (side - w) // 2
    oy = (side - h) // 2
    canvas[oy:oy + h, ox:ox + w] = img
    interp = cv2.INTER_AREA if side > size else cv2.INTER_LANCZOS4
    return cv2.resize(canvas, (size, size), interpolation=interp)
