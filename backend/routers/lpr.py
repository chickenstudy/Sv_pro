"""
Router LPR — đọc trực tiếp file JSON sidecar do AI Core ghi vào /Detect/lpr/.

Cấu trúc thư mục:
  /Detect/lpr/{source_id}/{YYYY-MM-DD}/{category}/{HHMMSS_xxx_label_plate}.json
                                                  + {prefix}_vehicle.jpg
                                                  + {prefix}_plate.jpg

JSON sidecar chứa đầy đủ:
  plate_number, plate_category, ocr_confidence, vehicle_bbox, plate_bbox_in_vehicle,
  files.{vehicle, plate}, timestamp, source_id, label.

Endpoints:
  GET /api/lpr/events?date=&category=&camera=&search=&limit=&offset=
  GET /api/lpr/stats?date=
  GET /api/lpr/event/{rel_path:path}      — Chi tiết JSON 1 event

Cách hoạt động:
  - Scan filesystem mỗi request (không cache) → luôn fresh.
  - Tốc độ: ~50ms cho 1 ngày với <2000 events.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, date as date_cls
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .auth import require_jwt

router = APIRouter()
logger = logging.getLogger("lpr_router")

_DETECT_ROOT = Path(os.environ.get("DETECT_DIR", "/Detect")).resolve()
_LPR_ROOT    = _DETECT_ROOT / "lpr"

# Phân loại biển — frontend filter dùng tên này
_KNOWN_CATEGORIES = {
    "XE_MAY_DAN_SU", "O_TO_DAN_SU", "BIEN_CA_NHAN",
    "XE_MAY_DIEN", "XE_QUAN_DOI", "KHONG_XAC_DINH", "NOT_DETECTED",
}


def _parse_one(json_path: Path, source_id: str, date_str: str, category: str) -> Optional[dict]:
    """Parse 1 file JSON sidecar → dict event (kèm derived path tương đối)."""
    try:
        with json_path.open() as f:
            data = json.load(f)
    except Exception as exc:
        logger.debug("Skip bad JSON %s: %s", json_path, exc)
        return None

    prefix = json_path.stem  # bỏ .json
    rel_dir = f"lpr/{source_id}/{date_str}/{category}"
    files = data.get("files") or {}
    vehicle_file = files.get("vehicle") or f"{prefix}_vehicle.jpg"
    plate_file   = files.get("plate")   or (f"{prefix}_plate.jpg" if category != "NOT_DETECTED" else None)

    vehicle_rel = f"{rel_dir}/{vehicle_file}" if (json_path.parent / vehicle_file).is_file() else None
    plate_rel   = f"{rel_dir}/{plate_file}"   if (plate_file and (json_path.parent / plate_file).is_file()) else None

    return {
        "id":              f"{rel_dir}/{prefix}",   # FE dùng làm key + lookup chi tiết
        "json_path":       f"{rel_dir}/{prefix}.json",
        "source_id":       data.get("source_id", source_id),
        "camera_id":       data.get("source_id", source_id),
        "date":            date_str,
        "category":        category,
        "label":           data.get("label"),                  # car/motorcycle/truck/bus
        "plate_number":    data.get("plate_number"),
        "plate_category":  data.get("plate_category", category),
        "ocr_confidence":  data.get("ocr_confidence"),
        "plate_det_confidence": data.get("plate_det_confidence"),
        "timestamp":       data.get("timestamp"),
        "image_path":      vehicle_rel,        # ảnh khung hình xe
        "plate_image_path": plate_rel,         # crop biển số
    }


def _scan_day(date_str: str, camera: Optional[str], category: Optional[str]) -> list[dict]:
    """Quét toàn bộ JSON sidecar trong /Detect/lpr/*/date/ (filter theo camera+category)."""
    if not _LPR_ROOT.is_dir():
        return []
    events: list[dict] = []

    sources = [camera] if camera else [p.name for p in _LPR_ROOT.iterdir() if p.is_dir()]
    for src in sources:
        src_dir = _LPR_ROOT / src / date_str
        if not src_dir.is_dir():
            continue
        cats = [category] if category else [p.name for p in src_dir.iterdir() if p.is_dir()]
        for cat in cats:
            cat_dir = src_dir / cat
            if not cat_dir.is_dir():
                continue
            for json_path in cat_dir.glob("*.json"):
                ev = _parse_one(json_path, src, date_str, cat)
                if ev is not None:
                    events.append(ev)
    # Sort mới nhất trước
    events.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    return events


@router.get("/events", summary="Danh sách LPR events từ /Detect/lpr/ JSON sidecar")
async def list_events(
    response: Response,
    _: Annotated[str, Depends(require_jwt)],
    date:     Optional[str] = Query(None, description="YYYY-MM-DD, default today"),
    category: Optional[str] = Query(None, description="XE_MAY_DAN_SU | O_TO_DAN_SU | BIEN_CA_NHAN | XE_QUAN_DOI | KHONG_XAC_DINH | NOT_DETECTED"),
    camera:   Optional[str] = Query(None, description="source_id (camera id)"),
    search:   Optional[str] = Query(None, description="Substring biển số"),
    limit:    int = 100,
    offset:   int = 0,
):
    """Trả danh sách event LPR đã đọc từ JSON sidecar trên disk."""
    if date is None:
        date = date_cls.today().isoformat()
    else:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date phải định dạng YYYY-MM-DD")

    if category and category not in _KNOWN_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"category không hợp lệ — chấp nhận {sorted(_KNOWN_CATEGORIES)}")

    events = _scan_day(date, camera, category)

    if search:
        s = search.upper().strip()
        events = [e for e in events if (e.get("plate_number") or "").upper().find(s) >= 0]

    total = len(events)
    response.headers["X-Total-Count"] = str(total)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"

    return events[offset: offset + limit]


@router.get("/stats", summary="Thống kê LPR theo phân loại + camera")
async def stats(
    _: Annotated[str, Depends(require_jwt)],
    date: Optional[str] = Query(None, description="YYYY-MM-DD, default today"),
):
    """Đếm events theo plate_category và camera cho 1 ngày."""
    if date is None:
        date = date_cls.today().isoformat()
    events = _scan_day(date, camera=None, category=None)

    by_category: dict[str, int] = {}
    by_camera:   dict[str, int] = {}
    for e in events:
        cat = e.get("plate_category") or "KHONG_XAC_DINH"
        cam = e.get("camera_id") or "?"
        by_category[cat] = by_category.get(cat, 0) + 1
        by_camera[cam]   = by_camera.get(cam, 0) + 1

    return {
        "date":         date,
        "total":        len(events),
        "by_category":  by_category,
        "by_camera":    [{"camera_id": c, "count": n} for c, n in sorted(by_camera.items(), key=lambda x: -x[1])],
    }


@router.get("/cameras", summary="Danh sách camera đã từng có LPR event")
async def list_cameras(_: Annotated[str, Depends(require_jwt)]):
    """Liệt kê thư mục con dưới /Detect/lpr/ → camera id."""
    if not _LPR_ROOT.is_dir():
        return []
    return sorted(p.name for p in _LPR_ROOT.iterdir() if p.is_dir())


@router.get("/event/{rel_path:path}", summary="Chi tiết 1 LPR event (đọc JSON sidecar)")
async def get_event(rel_path: str, _: Annotated[str, Depends(require_jwt)]):
    """
    rel_path = lpr/{source}/{date}/{category}/{prefix}  (không có .json)
    Trả về toàn bộ JSON sidecar content + paths ảnh đã resolve.
    """
    if rel_path.startswith("/") or ".." in rel_path.split("/"):
        raise HTTPException(status_code=400, detail="Đường dẫn không hợp lệ")
    json_path = (_DETECT_ROOT / f"{rel_path}.json").resolve()
    try:
        json_path.relative_to(_DETECT_ROOT)
    except ValueError:
        raise HTTPException(status_code=403, detail="Cấm truy cập ngoài /Detect")
    if not json_path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy event")

    parts = rel_path.split("/")
    if len(parts) < 5:
        raise HTTPException(status_code=400, detail="rel_path phải có dạng lpr/{src}/{date}/{cat}/{prefix}")
    _, source_id, date_str, category, _prefix = parts[0], parts[1], parts[2], parts[3], parts[4]
    ev = _parse_one(json_path, source_id, date_str, category)
    if ev is None:
        raise HTTPException(status_code=500, detail="JSON sidecar lỗi định dạng")

    # Kèm raw JSON cho FE muốn xem bbox
    with json_path.open() as f:
        ev["raw"] = json.load(f)
    return ev
