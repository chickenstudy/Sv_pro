import argparse
import json
import sys
from collections import defaultdict
from datetime import date as Date
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

GRID_SIZE = 50
MIN_CELL_HITS = 3
MIN_SUCCESS_RATE = 0.50


def load_detections_for_days(detect_dir: Path, camera: str, n_days: int) -> tuple[list[dict[str, Any]], list[str]]:
    cam_dir = detect_dir / camera
    if not cam_dir.exists():
        return [], []

    date_dirs = sorted(
        [d for d in cam_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True,
    )

    detections: list[dict[str, Any]] = []
    days_used: list[str] = []

    for day_dir in date_dirs:
        day_jsons = list(day_dir.rglob("*.json"))
        if not day_jsons:
            continue

        for jf in day_jsons:
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    detections.append(json.load(f))
            except Exception:
                continue

        days_used.append(day_dir.name)
        if len(days_used) >= n_days:
            break

    return detections, days_used


def compute_heatmap(detections: list[dict[str, Any]], frame_w: int, frame_h: int, grid: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cols = (frame_w + grid - 1) // grid
    rows = (frame_h + grid - 1) // grid

    total = np.zeros((rows, cols), dtype=np.int32)
    success = np.zeros((rows, cols), dtype=np.int32)

    for det in detections:
        bbox = det.get("vehicle_bbox") or {}
        x1, y1, x2, y2 = bbox.get("x1", 0), bbox.get("y1", 0), bbox.get("x2", 0), bbox.get("y2", 0)
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

        c = min(cx // grid, cols - 1)
        r = min(cy // grid, rows - 1)

        total[r, c] += 1
        if det.get("plate_category") != "KHONG_XAC_DINH":
            success[r, c] += 1

    with np.errstate(invalid="ignore"):
        rate = np.where(total > 0, success / total, np.nan)

    return total, success, rate


def suggest_roi(total: np.ndarray, rate: np.ndarray, grid: int, min_hits: int = MIN_CELL_HITS, min_rate: float = MIN_SUCCESS_RATE) -> tuple[int, int, int, int] | None:
    good = (total >= min_hits) & (rate >= min_rate)
    if not good.any():
        good = total >= min_hits
    if not good.any():
        return None

    rows_ok, cols_ok = np.where(good)
    r0, r1 = int(rows_ok.min()), int(rows_ok.max())
    c0, c1 = int(cols_ok.min()), int(cols_ok.max())

    # roi: [x1,y1,x2,y2] in pixel coordinates
    return (c0 * grid, r0 * grid, (c1 + 1) * grid, (r1 + 1) * grid)


def load_current_roi(module_yml: Path, camera: str) -> list[int] | None:
    try:
        cfg = yaml.safe_load(module_yml.read_text(encoding="utf-8"))
    except Exception:
        return None

    elements = (cfg or {}).get("pipeline", {}).get("elements", [])
    for elem in elements:
        if elem.get("element") != "pyfunc":
            continue
        kwargs = elem.get("kwargs") or {}
        zones = kwargs.get("roi_zones") or {}
        if camera in zones:
            return list(zones[camera])

    return None


def apply_roi_to_module_yml(module_yml: Path, camera: str, new_roi: list[int]) -> bool:
    try:
        cfg = yaml.safe_load(module_yml.read_text(encoding="utf-8"))
    except Exception:
        return False

    elements = (cfg or {}).get("pipeline", {}).get("elements", [])
    if not isinstance(elements, list):
        return False

    applied = False
    for elem in elements:
        if elem.get("element") != "pyfunc":
            continue
        kwargs = elem.setdefault("kwargs", {})
        zones = kwargs.setdefault("roi_zones", {})
        if not isinstance(zones, dict):
            zones = {}
            kwargs["roi_zones"] = zones
        if camera in zones or True:
            zones[camera] = list(new_roi)
            applied = True

    if not applied:
        return False

    # Atomic-ish write: write to temp then replace.
    tmp_path = module_yml.with_suffix(".yml.tmp")
    tmp_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    tmp_path.replace(module_yml)
    return True


def restart_container(container_name: str) -> bool:
    if not container_name:
        return False
    try:
        import docker as docker_sdk

        client = docker_sdk.from_env()
        client.containers.get(container_name).restart()
        return True
    except Exception:
        return False


def save_heatmap(
    total: np.ndarray,
    rate: np.ndarray,
    suggested_roi: tuple[int, int, int, int] | None,
    current_roi: list[int] | None,
    out_path: Path,
    grid: int,
    frame_w: int,
    frame_h: int,
) -> None:
    h, w = total.shape
    img = np.zeros((h * grid, w * grid, 3), dtype=np.uint8)

    for r in range(h):
        for c in range(w):
            y1, x1 = r * grid, c * grid
            y2 = min(y1 + grid, frame_h)
            x2 = min(x1 + grid, frame_w)

            n = int(total[r, c])
            sr = float(rate[r, c]) if not np.isnan(rate[r, c]) else np.nan

            if n == 0:
                color = (20, 20, 20)
            elif np.isnan(sr):
                color = (80, 80, 80)
            else:
                g_ch = int(sr * 220)
                r_ch = int((1.0 - sr) * 220)
                color = (0, g_ch, r_ch)  # BGR

            cv2.rectangle(img, (x1, y1), (x2, y2), color, -1)

            if n >= MIN_CELL_HITS:
                label = f"{int(sr * 100) if not np.isnan(sr) else 0}%"
                cv2.putText(img, label, (x1 + 2, y2 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.28, (230, 230, 230), 1)

    if current_roi:
        cx1, cy1, cx2, cy2 = current_roi
        cv2.rectangle(img, (cx1, cy1), (cx2, cy2), (0, 200, 255), 2)
        cv2.putText(img, "Current ROI", (cx1 + 4, cy1 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)

    if suggested_roi:
        sx1, sy1, sx2, sy2 = suggested_roi
        cv2.rectangle(img, (sx1, sy1), (sx2, sy2), (255, 230, 0), 3)
        cv2.putText(img, "Suggested ROI", (sx1 + 4, sy1 + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 230, 0), 2)

    cv2.imwrite(str(out_path), img)


def evaluate_camera(
    detect_dir: Path,
    roi_dir: Path,
    module_yml: Path,
    camera: str,
    today: str,
    n_days: int,
    frame_w: int,
    frame_h: int,
    auto_apply: bool = False,
) -> dict[str, Any]:
    detections, days_used = load_detections_for_days(detect_dir, camera, n_days)
    current_roi = load_current_roi(module_yml, camera)

    out_dir = roi_dir / today
    out_dir.mkdir(parents=True, exist_ok=True)

    if not detections:
        report = {
            "date": today,
            "camera": camera,
            "days_used": [],
            "total_detections": 0,
            "note": "No data available — keeping current ROI unchanged.",
            "current_roi": current_roi,
            "suggested_roi": current_roi,
        }
        (out_dir / f"{camera}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return report

    xs = [d["vehicle_bbox"]["x2"] for d in detections if isinstance(d.get("vehicle_bbox"), dict) and "x2" in d["vehicle_bbox"]]
    ys = [d["vehicle_bbox"]["y2"] for d in detections if isinstance(d.get("vehicle_bbox"), dict) and "y2" in d["vehicle_bbox"]]
    if xs:
        frame_w = max(frame_w, max(xs))
    if ys:
        frame_h = max(frame_h, max(ys))

    total_grid, _, rate_grid = compute_heatmap(detections, frame_w, frame_h, GRID_SIZE)
    suggested_roi = suggest_roi(total_grid, rate_grid, GRID_SIZE)

    n_total = len(detections)
    cats = defaultdict(int)
    n_success = 0
    for d in detections:
        cat = d.get("plate_category", "?")
        cats[cat] += 1
        if cat != "KHONG_XAC_DINH":
            n_success += 1

    heatmap_file = f"{camera}_heatmap.jpg"
    report: dict[str, Any] = {
        "date": today,
        "camera": camera,
        "days_used": days_used,
        "frame_size": {"width": frame_w, "height": frame_h},
        "total_detections": n_total,
        "successful_detections": n_success,
        "overall_success_rate": round(n_success / n_total, 4) if n_total else 0,
        "categories": dict(sorted(cats.items(), key=lambda x: -x[1])),
        "current_roi": current_roi,
        "suggested_roi": list(suggested_roi) if suggested_roi else current_roi,
        "roi_changed": suggested_roi is not None and list(suggested_roi) != current_roi,
        "heatmap_file": heatmap_file,
    }

    if report["suggested_roi"]:
        report["suggested_roi"] = [int(v) for v in report["suggested_roi"]]

    (out_dir / f"{camera}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    save_heatmap(
        total_grid,
        rate_grid,
        suggested_roi,
        current_roi,
        out_dir / heatmap_file,
        GRID_SIZE,
        frame_w,
        frame_h,
    )

    if auto_apply and report.get("roi_changed") and report.get("suggested_roi"):
        ok = apply_roi_to_module_yml(module_yml, camera, report["suggested_roi"])
        report["auto_applied"] = ok

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily ROI evaluation for LPR")
    parser.add_argument("--detect-dir", default="./Detect", help="Detect base directory")
    parser.add_argument("--roi-dir", default="./ROI", help="ROI output directory")
    parser.add_argument("--module-yml", default="./module/module.yml", help="Savant module YAML")
    parser.add_argument("--days", type=int, default=3, help="Number of recent days to use")
    parser.add_argument("--auto-apply", action="store_true", default=False, help="Write suggested ROI back to module.yml")
    parser.add_argument("--frame-width", type=int, default=2592)
    parser.add_argument("--frame-height", type=int, default=1944)

    args = parser.parse_args()

    detect_dir = Path(args.detect_dir)
    roi_dir = Path(args.roi_dir)
    module_yml = Path(args.module_yml)
    today = str(Date.today())

    if not detect_dir.exists():
        print(f"ERROR: Detect dir not found: {detect_dir}", file=sys.stderr)
        sys.exit(1)

    cameras = sorted([p.name for p in detect_dir.iterdir() if p.is_dir()])
    print(f"ROI evaluation — date={today}, cameras={cameras}, last {args.days} days")

    for camera in cameras:
        _ = evaluate_camera(
            detect_dir=detect_dir,
            roi_dir=roi_dir,
            module_yml=module_yml,
            camera=camera,
            today=today,
            n_days=args.days,
            frame_w=args.frame_width,
            frame_h=args.frame_height,
            auto_apply=args.auto_apply,
        )

    print("Done.")


if __name__ == "__main__":
    main()

