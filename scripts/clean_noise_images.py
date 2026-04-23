#!/usr/bin/env python3
"""
Clean Noise Images — quét toàn bộ Detect/faces/, dùng YOLOv8-face + quality
filter để verify lại từng ảnh đã lưu. Ảnh nào KHÔNG pass → xóa file + sync DB.

Quy tắc giữ lại:
  1. YOLOv8-face detect ≥ 1 face với conf ≥ 0.55
  2. Bbox face ≥ 80×80 px (frame gốc — không phải crop)
  3. Composite quality score ≥ 0.50

Việc tự sync DB:
  - DELETE recognition_logs có metadata_json->image_path = ảnh đã xóa
  - DELETE guest_faces nào không còn ảnh nào trong logs (CASCADE xóa stranger_embeddings)

Default DRY-RUN. Truyền --apply để xóa thật.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

import cv2
import psycopg2

# Đảm bảo có thể import src.fr.yolov8_face từ /opt/savant/user_data
ROOT = Path("/opt/savant/user_data")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.fr.yolov8_face import YOLOv8FaceDetector
from src.fr.face_quality import compute_quality_score, _MIN_COMPOSITE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] clean-noise: %(message)s",
)
log = logging.getLogger(__name__)

DB_DSN = os.environ.get("POSTGRES_DSN",
                        "postgresql://svpro_user:svpro_pass@postgres:5432/svpro_db")
DETECT_ROOT = Path(os.environ.get("DETECT_ROOT", "/Detect/faces"))
YOLOV8_FACE_PATH = "/models/yolov8_face/yolov8n-face.onnx"

# Ngưỡng (đồng bộ với face_recognizer)
MIN_FACE_PX  = 80
MIN_CONF     = 0.55
MIN_QUALITY  = _MIN_COMPOSITE   # 0.50


def _classify(img_path: Path, det: YOLOv8FaceDetector) -> tuple[str, dict]:
    """
    Trả về (verdict, info) — verdict ∈ {"keep", "no_face", "small_face", "low_quality", "read_error"}.
    """
    img = cv2.imread(str(img_path))
    if img is None:
        return "read_error", {}

    dets = det.detect(img)
    if not dets:
        return "no_face", {"shape": img.shape}

    # Pick best score
    bbox, score, kps = max(dets, key=lambda d: d[1])
    bw = bbox[2] - bbox[0]
    bh = bbox[3] - bbox[1]
    if bw < MIN_FACE_PX or bh < MIN_FACE_PX:
        return "small_face", {"bbox_w": bw, "bbox_h": bh, "score": score}

    # Quality check (cần landmarks — YOLOv8-face có sẵn)
    # face_aligned không có sẵn vì ảnh đã save; dùng crop trực tiếp từ bbox
    x1, y1, x2, y2 = bbox
    face_crop = img[max(0, y1):y2, max(0, x1):x2]
    if face_crop.size == 0:
        return "no_face", {}

    # Resize về 112×112 cho quality eval (gần với pipeline)
    face112 = cv2.resize(face_crop, (112, 112), interpolation=cv2.INTER_LINEAR)
    quality, _ = compute_quality_score(face112, kps)
    if quality < MIN_QUALITY:
        return "low_quality", {"quality": quality, "score": score}

    return "keep", {"score": score, "quality": quality, "bbox": (bw, bh)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Thực sự xóa file + DB. Mặc định DRY-RUN.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Giới hạn số ảnh check (0 = all). Dùng để test.")
    args = parser.parse_args()

    log.info("Loading YOLOv8-face: %s", YOLOV8_FACE_PATH)
    det = YOLOv8FaceDetector(
        model_path=YOLOV8_FACE_PATH,
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        conf_thresh=MIN_CONF,
    )

    log.info("Scanning %s ...", DETECT_ROOT)
    all_imgs = sorted(DETECT_ROOT.rglob("*_face.jpg"))
    if args.limit > 0:
        all_imgs = all_imgs[:args.limit]
    log.info("Found %d face images", len(all_imgs))

    counts: Counter = Counter()
    to_delete: list[Path] = []

    for i, img_path in enumerate(all_imgs):
        if i > 0 and i % 200 == 0:
            log.info("Progress: %d/%d (kept=%d, drop=%d)",
                     i, len(all_imgs), counts["keep"], len(to_delete))
        verdict, info = _classify(img_path, det)
        counts[verdict] += 1
        if verdict != "keep":
            to_delete.append(img_path)

    log.info("=== Verdict summary ===")
    for k, v in counts.most_common():
        log.info("  %-12s : %5d  (%.1f%%)", k, v, 100 * v / max(len(all_imgs), 1))

    if not to_delete:
        log.info("Không có ảnh nào cần xóa — DB sạch.")
        return

    log.info("Marked for deletion: %d images", len(to_delete))
    sample = to_delete[:5]
    log.info("Sample: %s", [p.name for p in sample])

    if not args.apply:
        log.info("[DRY-RUN] không xóa gì. Re-run với --apply.")
        return

    # ── APPLY: xóa file + sidecar JSON + DB rows ─────────────────────────
    log.info("Connecting DB ...")
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = False
    cur = conn.cursor()

    # Build relative paths để match metadata_json->image_path
    # image_path lưu kiểu "faces/{cam}/{date}/unknown/xxx_Stranger_face.jpg"
    rel_paths: list[str] = []
    files_deleted = 0
    sidecars_deleted = 0
    for p in to_delete:
        try:
            rel = p.relative_to(DETECT_ROOT.parent)   # /Detect → faces/...
        except ValueError:
            rel = p.relative_to(DETECT_ROOT)
        rel_paths.append(str(rel).replace(os.sep, "/"))

        # Xóa file ảnh
        try:
            p.unlink()
            files_deleted += 1
        except FileNotFoundError:
            pass

        # Xóa sidecar JSON cùng tên (xxx_face.jpg → xxx.json)
        sidecar = p.with_name(p.stem.removesuffix("_face") + ".json")
        try:
            sidecar.unlink()
            sidecars_deleted += 1
        except FileNotFoundError:
            pass

    log.info("Deleted: %d files + %d sidecar JSONs", files_deleted, sidecars_deleted)

    # ── DB sync ──────────────────────────────────────────────────────────
    # Xóa recognition_logs row có image_path khớp
    BATCH = 500
    total_rl_deleted = 0
    for i in range(0, len(rel_paths), BATCH):
        batch = rel_paths[i:i + BATCH]
        cur.execute(
            "DELETE FROM recognition_logs "
            "WHERE metadata_json->>'image_path' = ANY(%s)",
            (batch,),
        )
        total_rl_deleted += cur.rowcount
    log.info("Deleted %d recognition_logs rows", total_rl_deleted)

    # Cập nhật last_image_path trong guest_faces nếu trỏ tới ảnh đã xóa
    cur.execute(
        """UPDATE guest_faces gf
           SET metadata_json = metadata_json - 'last_image_path'
           WHERE metadata_json->>'last_image_path' = ANY(%s)""",
        (rel_paths,),
    )
    log.info("Cleared last_image_path in %d guest_faces", cur.rowcount)

    # Re-pick last_image_path từ recognition_logs còn lại
    cur.execute(
        """UPDATE guest_faces gf
           SET metadata_json = jsonb_set(
               COALESCE(metadata_json, '{}'),
               '{last_image_path}',
               to_jsonb((
                   SELECT rl.metadata_json->>'image_path'
                   FROM recognition_logs rl
                   WHERE rl.person_id = gf.stranger_id
                     AND rl.is_stranger = TRUE
                     AND rl.metadata_json->>'image_path' IS NOT NULL
                   ORDER BY rl.created_at DESC LIMIT 1
               ))
           )
           WHERE NOT (metadata_json ? 'last_image_path')
             AND EXISTS (
                 SELECT 1 FROM recognition_logs rl
                 WHERE rl.person_id = gf.stranger_id AND rl.is_stranger = TRUE
                   AND rl.metadata_json->>'image_path' IS NOT NULL
             )"""
    )
    log.info("Re-pointed last_image_path for %d strangers", cur.rowcount)

    # Xóa stranger không còn ảnh nào trong logs
    cur.execute(
        """DELETE FROM guest_faces gf
           WHERE NOT EXISTS (
               SELECT 1 FROM recognition_logs rl
               WHERE rl.person_id = gf.stranger_id AND rl.is_stranger = TRUE
                 AND rl.metadata_json->>'image_path' IS NOT NULL
           )"""
    )
    log.info("Deleted %d orphan strangers (no images left)", cur.rowcount)

    conn.commit()
    cur.close()
    conn.close()
    log.info("[APPLIED] Cleanup complete.")


if __name__ == "__main__":
    main()
