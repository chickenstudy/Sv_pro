"""
Backend Metrics Router — Sprint 5.

GET /api/metrics/summary   → Tóm tắt trạng thái pipeline (queue, fps, drops).
GET /api/metrics/pipeline  → Chi tiết từng camera.
GET /api/metrics/watchdog  → Trạng thái watchdog + circuit breakers.

Dữ liệu lấy từ:
  1. Prometheus HTTP API (nếu Prometheus up).
  2. Fallback: trực tiếp từ in-process metrics counters (nếu chạy cùng AI core).
"""

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter()

_PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _query_prometheus(query: str) -> list[dict]:
    """Gọi Prometheus instant query API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_PROMETHEUS_URL}/api/v1/query",
                params={"query": query},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("result", [])
    except Exception as exc:
        logger.warning("Prometheus query failed [%s]: %s", query, exc)
        return []


def _extract_metric(results: list[dict], label: str = "camera_id") -> dict[str, float]:
    """Trích xuất {label_value: float_value} từ Prometheus instant result."""
    out = {}
    for r in results:
        key = r.get("metric", {}).get(label, "unknown")
        try:
            out[key] = float(r["value"][1])
        except (KeyError, IndexError, ValueError):
            out[key] = 0.0
    return out


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/summary",
    summary="Pipeline metrics summary",
    response_model=None,
)
async def get_metrics_summary() -> dict[str, Any]:
    """
    Tóm tắt nhanh trạng thái pipeline:
    - FPS ingress theo camera
    - Queue depth AI core
    - Drop rate tổng hợp
    - JSON egress rate
    """
    # In practice, AI core is the reliable source of effective FPS:
    # we approximate "ingress FPS" with frames actually processed.
    fps_results   = await _query_prometheus("rate(svpro_frames_processed_total[1m])")
    queue_results = await _query_prometheus("svpro_aicore_queue_depth")
    drop_results  = await _query_prometheus("sum by (camera_id) (rate(svpro_dropped_total[1m]))")
    # JSON egress adapter does not emit svpro_egress_json_rate directly;
    # use produced events as a proxy for "egress rate".
    egress_results = await _query_prometheus(
        "sum by (camera_id) (rate(svpro_lpr_events_total[1m]) + rate(svpro_fr_events_total[1m]))"
    )

    fps_by_camera   = _extract_metric(fps_results, label="source_id")
    queue_by_camera = _extract_metric(queue_results)
    drop_by_camera  = _extract_metric(drop_results)
    egress_by_camera = _extract_metric(egress_results)

    cameras = sorted(set(
        list(fps_by_camera.keys()) +
        list(queue_by_camera.keys()) +
        list(drop_by_camera.keys())
    ))

    camera_stats = []
    for cam in cameras:
        if cam == "unknown":
            continue
        camera_stats.append({
            "camera_id":     cam,
            "fps":           round(fps_by_camera.get(cam, 0.0), 2),
            "queue_depth":   round(queue_by_camera.get(cam, 0.0), 1),
            "drop_rate_1m":  round(drop_by_camera.get(cam, 0.0), 3),
            "egress_rate":   round(egress_by_camera.get(cam, 0.0), 2),
        })

    overall_fps   = sum(v for v in fps_by_camera.values())
    overall_drops = sum(v for v in drop_by_camera.values())

    return {
        "status": "ok",
        "prometheus_url": _PROMETHEUS_URL,
        "overall": {
            "total_fps":     round(overall_fps, 2),
            "total_drop_1m": round(overall_drops, 4),
            "camera_count":  len(camera_stats),
        },
        "cameras": camera_stats,
    }


@router.get(
    "/pipeline",
    summary="Detailed pipeline metrics per camera",
    response_model=None,
)
async def get_pipeline_detail() -> dict[str, Any]:
    """
    Chi tiết inference latency + LPR/FR breakdowns theo camera.
    """
    lpr_total  = await _query_prometheus("sum by (camera_id, result) (svpro_lpr_ocr_total)")
    fr_total   = await _query_prometheus("sum by (camera_id, result) (svpro_fr_recognition_total)")
    infer_p95  = await _query_prometheus(
        "histogram_quantile(0.95, sum by (camera_id, model, le) "
        "(rate(svpro_aicore_inference_ms_bucket[5m])))"
    )

    # Group LPR results by camera
    lpr_by_camera: dict[str, dict] = {}
    for r in lpr_total:
        cam    = r.get("metric", {}).get("camera_id", "unknown")
        result = r.get("metric", {}).get("result", "unknown")
        val    = float(r["value"][1]) if r.get("value") else 0.0
        lpr_by_camera.setdefault(cam, {})[result] = val

    # Group FR results by camera
    fr_by_camera: dict[str, dict] = {}
    for r in fr_total:
        cam    = r.get("metric", {}).get("camera_id", "unknown")
        result = r.get("metric", {}).get("result", "unknown")
        val    = float(r["value"][1]) if r.get("value") else 0.0
        fr_by_camera.setdefault(cam, {})[result] = val

    # Group inference P95 by camera+model
    infer_by_cam_model: dict[str, dict] = {}
    for r in infer_p95:
        cam   = r.get("metric", {}).get("camera_id", "unknown")
        model = r.get("metric", {}).get("model", "unknown")
        val   = float(r["value"][1]) if r.get("value") else 0.0
        infer_by_cam_model.setdefault(cam, {})[model] = round(val, 2)

    cameras = sorted(set(
        list(lpr_by_camera.keys()) +
        list(fr_by_camera.keys()) +
        list(infer_by_cam_model.keys())
    ) - {"unknown"})

    detail = []
    for cam in cameras:
        detail.append({
            "camera_id":       cam,
            "lpr":             lpr_by_camera.get(cam, {}),
            "fr":              fr_by_camera.get(cam, {}),
            "inference_p95_ms": infer_by_cam_model.get(cam, {}),
        })

    return {"cameras": detail}


@router.get(
    "/watchdog",
    summary="Watchdog and circuit breaker status",
    response_model=None,
)
async def get_watchdog_status() -> dict[str, Any]:
    """
    Trạng thái watchdog và circuit breakers.
    Lấy từ Prometheus gauge `svpro_watchdog_circuit_open` và `svpro_watchdog_restarts_total`.
    """
    circuit_results  = await _query_prometheus("svpro_watchdog_circuit_open")
    restart_results  = await _query_prometheus("svpro_watchdog_restarts_total")

    circuit_by_comp  = _extract_metric(circuit_results, label="component")
    restarts_by_comp = _extract_metric(restart_results, label="component")

    components = sorted(set(
        list(circuit_by_comp.keys()) + list(restarts_by_comp.keys())
    ) - {"unknown"})

    status_list = []
    for comp in components:
        circuit_open = circuit_by_comp.get(comp, 0.0) > 0.5
        status_list.append({
            "component":      comp,
            "circuit_open":   circuit_open,
            "total_restarts": int(restarts_by_comp.get(comp, 0)),
            "status":         "degraded" if circuit_open else "ok",
        })

    overall = "ok"
    if any(s["circuit_open"] for s in status_list):
        overall = "degraded"

    return {
        "overall_status": overall,
        "components": status_list,
    }
