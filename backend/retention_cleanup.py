"""
Retention cleanup — xoá file ảnh + DB row cũ hơn ngưỡng cấu hình.

Chạy:
  - Tự động: APScheduler cron mỗi ngày 02:15 (đăng ký trong main.py lifespan).
  - Thủ công: POST /api/settings/cleanup/run từ FE.

Đọc cấu hình từ bảng app_settings (key prefix `retention.*`):
  retention.detect_days       → /Detect/**/*.jpg|.json
  retention.audit_days        → /Detect/audit/**/*.json (nếu BE riêng audit)
  retention.events_days       → access_events + recognition_logs (DELETE)
  retention.guest_faces_days  → guest_faces

Bảo mật:
  - Mọi xoá file đều resolve absolute và check prefix /Detect/ → không thể path traversal.
  - DELETE SQL có WHERE event_timestamp < NOW() - INTERVAL — không xoá full table.
  - Min 1 ngày cho mọi setting (validation ở router).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from . import database  # truy cập database.pool động (init xong mới có)

logger = logging.getLogger("retention_cleanup")


def _pool():
    return database.pool

_DETECT_ROOT = Path(os.environ.get("DETECT_DIR", "/Detect")).resolve()
_AUDIT_ROOT  = _DETECT_ROOT / "audit"


async def _get_settings(con) -> dict[str, Any]:
    """Đọc retention.* từ app_settings."""
    rows = await con.fetch(
        "SELECT key, value FROM app_settings WHERE key LIKE 'retention.%'"
    )
    out = {}
    for r in rows:
        # value là JSONB → asyncpg trả str/int trực tiếp tuỳ JSON type
        v = r["value"]
        if isinstance(v, str) and v.isdigit():
            v = int(v)
        out[r["key"]] = v
    return out


def _delete_old_files(root: Path, days: int) -> tuple[int, int]:
    """
    Walk root recursively, xoá file mtime cũ hơn `days` ngày.
    Trả về (n_deleted, total_bytes).
    Bỏ qua mọi path không nằm trong _DETECT_ROOT (an toàn).
    """
    if days < 1:
        return 0, 0
    if not root.exists():
        return 0, 0
    try:
        root_resolved = root.resolve()
        if not str(root_resolved).startswith(str(_DETECT_ROOT)):
            logger.warning("Refuse to clean outside /Detect: %s", root_resolved)
            return 0, 0
    except Exception:
        return 0, 0

    cutoff = time.time() - days * 86400
    deleted = 0
    total_bytes = 0
    for path in root_resolved.rglob("*"):
        if not path.is_file():
            continue
        try:
            st = path.stat()
            if st.st_mtime < cutoff:
                size = st.st_size
                path.unlink()
                deleted += 1
                total_bytes += size
        except OSError as exc:
            logger.debug("unlink %s failed: %s", path, exc)

    # Xoá thư mục rỗng còn lại (date folders)
    for path in sorted(root_resolved.rglob("*"), key=lambda p: -len(p.parts)):
        if path.is_dir():
            try:
                path.rmdir()  # only succeeds if empty
            except OSError:
                pass

    return deleted, total_bytes


async def run_cleanup_now(triggered_by: str = "cron") -> dict:
    """
    Thực thi 1 lần cleanup. Tạo row trong retention_runs để audit.
    Trả về summary dict cho caller (FE hoặc log).
    """
    if _pool() is None:
        raise RuntimeError("DB pool chưa khởi tạo")

    async with _pool().acquire() as con:
        # Tạo row run mới
        run_id = await con.fetchval(
            "INSERT INTO retention_runs (triggered_by) VALUES ($1) RETURNING id",
            triggered_by,
        )
        settings = await _get_settings(con)

    detect_days = int(settings.get("retention.detect_days", 30))
    audit_days  = int(settings.get("retention.audit_days", 90))
    events_days = int(settings.get("retention.events_days", 180))
    guest_days  = int(settings.get("retention.guest_faces_days", 7))

    summary: dict[str, Any] = {
        "run_id":       run_id,
        "triggered_by": triggered_by,
        "files_deleted": 0,
        "bytes_deleted": 0,
        "rows_deleted":  {},
        "error":         None,
    }

    started = time.time()
    try:
        # ── 1. Files /Detect (faces + plates), trừ subfolder audit ──────
        detect_n, detect_b = 0, 0
        for child in _DETECT_ROOT.iterdir() if _DETECT_ROOT.exists() else []:
            if child.name == "audit":
                continue
            if child.is_dir():
                n, b = _delete_old_files(child, detect_days)
                detect_n += n; detect_b += b

        # ── 2. Audit subdir riêng ngưỡng ────────────────────────────────
        audit_n, audit_b = _delete_old_files(_AUDIT_ROOT, audit_days)

        files_deleted = detect_n + audit_n
        bytes_deleted = detect_b + audit_b
        summary["files_deleted"] = files_deleted
        summary["bytes_deleted"] = bytes_deleted

        # ── 3. DB rows ──────────────────────────────────────────────────
        async with _pool().acquire() as con:
            ev_count = await con.fetchval(
                f"WITH d AS (DELETE FROM access_events "
                f"WHERE event_timestamp < NOW() - INTERVAL '{events_days} days' RETURNING 1) "
                f"SELECT COUNT(*) FROM d"
            )
            rec_count = await con.fetchval(
                f"WITH d AS (DELETE FROM recognition_logs "
                f"WHERE created_at < NOW() - INTERVAL '{events_days} days' RETURNING 1) "
                f"SELECT COUNT(*) FROM d"
            )
            guest_count = await con.fetchval(
                f"WITH d AS (DELETE FROM guest_faces "
                f"WHERE last_seen < NOW() - INTERVAL '{guest_days} days' RETURNING 1) "
                f"SELECT COUNT(*) FROM d"
            )

            summary["rows_deleted"] = {
                "access_events":    ev_count or 0,
                "recognition_logs": rec_count or 0,
                "guest_faces":      guest_count or 0,
            }

            await con.execute(
                """UPDATE retention_runs SET
                       finished_at   = NOW(),
                       deleted_files = $1,
                       deleted_bytes = $2,
                       deleted_rows  = $3
                   WHERE id = $4""",
                files_deleted, bytes_deleted,
                summary["rows_deleted"],
                run_id,
            )

        elapsed = time.time() - started
        logger.info(
            "Retention cleanup done in %.1fs: %d files (%d MB), rows=%s",
            elapsed, files_deleted, bytes_deleted // 1_000_000, summary["rows_deleted"],
        )
        summary["took_seconds"] = round(elapsed, 2)

    except Exception as exc:
        logger.error("Retention cleanup error: %s", exc, exc_info=True)
        summary["error"] = str(exc)
        try:
            async with _pool().acquire() as con:
                await con.execute(
                    "UPDATE retention_runs SET finished_at = NOW(), error = $1 WHERE id = $2",
                    str(exc)[:1000], run_id,
                )
        except Exception:
            pass

    return summary


async def ensure_recognition_log_partitions(months_ahead: int = 2) -> None:
    """
    Đảm bảo có sẵn partition cho recognition_logs các tháng sắp tới.
    Bảng được partition theo RANGE(created_at) — nếu thiếu partition,
    INSERT sẽ ERROR. Job này tạo trước 2 tháng tới.
    """
    if _pool() is None:
        return
    from datetime import datetime, timedelta
    today = datetime.utcnow().replace(day=1)
    async with _pool().acquire() as con:
        for i in range(0, months_ahead + 1):
            year  = today.year
            month = today.month + i
            while month > 12:
                month -= 12
                year  += 1
            next_year, next_month = year, month + 1
            if next_month > 12:
                next_month -= 12
                next_year  += 1
            part = f"recognition_logs_{year}_{month:02d}"
            try:
                await con.execute(
                    f"""CREATE TABLE IF NOT EXISTS {part}
                        PARTITION OF recognition_logs
                        FOR VALUES FROM ('{year}-{month:02d}-01')
                        TO ('{next_year}-{next_month:02d}-01')"""
                )
                logger.debug("ensured partition %s", part)
            except Exception as exc:
                logger.warning("create partition %s failed: %s", part, exc)
