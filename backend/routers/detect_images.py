"""
Router serve ảnh detection do AI Core ghi xuống /Detect/.

AI Core (FR pyfunc + LPR pyfunc) lưu file vào volume bind /Detect:
  /Detect/faces/{cam}/{date}/{role}/{ts}_{id}_{name}_face.jpg
  /Detect/{cam}/{date}/{category}/{ts}_{plate}.jpg

Backend mount cùng volume readonly và serve qua endpoint:
  GET /api/detect-images/{rel_path:path}

Bảo mật:
  - rel_path resolve về absolute, phải nằm trong _DETECT_ROOT (chống path traversal).
  - Chỉ trả về file *.jpg / *.png / *.json (không cho liệt kê thư mục).
  - JWT bắt buộc — không expose anonymously.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .auth import require_jwt_query_or_header

router = APIRouter()
logger = logging.getLogger("detect_images")

_DETECT_ROOT = Path(os.environ.get("DETECT_DIR", "/Detect")).resolve()
_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".json"}


def _safe_resolve(rel_path: str) -> Path:
    """Resolve rel_path inside _DETECT_ROOT, raise 400/403 nếu vi phạm."""
    if not rel_path or rel_path.startswith("/") or ".." in rel_path.split("/"):
        raise HTTPException(status_code=400, detail="Đường dẫn không hợp lệ")
    candidate = (_DETECT_ROOT / rel_path).resolve()
    try:
        candidate.relative_to(_DETECT_ROOT)
    except ValueError:
        raise HTTPException(status_code=403, detail="Cấm truy cập ngoài /Detect")
    if candidate.suffix.lower() not in _ALLOWED_EXT:
        raise HTTPException(status_code=415, detail=f"Loại file không được phép: {candidate.suffix}")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh")
    return candidate


@router.get(
    "/api/detect-images/{rel_path:path}",
    summary="Serve ảnh detection từ /Detect/",
)
async def get_detect_image(rel_path: str, _=Depends(require_jwt_query_or_header)):
    """
    Trả về file ảnh hoặc JSON sidecar do AI Core ghi vào /Detect/.
    Path tương đối, ví dụ: faces/cam_online_1/2026-04-20/unknown/143253_…_face.jpg
    """
    path = _safe_resolve(rel_path)
    media_type = (
        "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg")
        else "image/png" if path.suffix.lower() == ".png"
        else "application/json"
    )
    return FileResponse(path, media_type=media_type)
