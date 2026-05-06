"""
Migration script: tổ chức lại thư mục /Detect/ theo nghiệp vụ.

Cũ:
  /Detect/{source_id}/{date}/{category}/   → LPR crops
  /Detect/faces/{source_id}/{date}/{role}/ → Face crops
  /Detect/audit/{date}/{event_type}/{source_id}/ → Behavior JSON

Mới:
  /Detect/lpr/{source_id}/{date}/{category}/
  /Detect/face/{source_id}/{date}/{role}/
  /Detect/behavior/{source_id}/{date}/{event_type}/

Chạy: python3 scripts/migrate_detect_dirs.py [--dry-run] [--update-db]
"""
import argparse
import json
import os
import shutil
import sys

DETECT_ROOT = "/Detect"
KNOWN_SKIP  = {"lpr", "face", "behavior", "faces", "audit"}   # thư mục không phải source_id


def move(src: str, dst: str, dry: bool) -> bool:
    if not os.path.exists(src):
        return False
    if dry:
        print(f"  [DRY] {src} → {dst}")
        return True
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    print(f"  MOVED {src} → {dst}")
    return True


def migrate_lpr(dry: bool) -> dict:
    """
    /Detect/{source_id}/ → /Detect/lpr/{source_id}/
    Bỏ qua: lpr, face, behavior, faces, audit, cam_online_1 (nếu rỗng)
    """
    mapping = {}
    for entry in os.listdir(DETECT_ROOT):
        src_path = os.path.join(DETECT_ROOT, entry)
        if not os.path.isdir(src_path):
            continue
        if entry in KNOWN_SKIP:
            continue
        if entry in {"cam_online_1", "cam2", "cam2_1", "cam-face", "bien_xe"} or \
           (entry not in {"faces", "audit", "lpr", "face", "behavior"}):
            # Di chuyển toàn bộ thư mục source vào /Detect/lpr/
            dst_path = os.path.join(DETECT_ROOT, "lpr", entry)
            if not dry:
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            print(f"LPR: {entry}/ → lpr/{entry}/")
            for date_dir in os.listdir(src_path):
                src_date = os.path.join(src_path, date_dir)
                dst_date = os.path.join(dst_path, date_dir)
                if os.path.isdir(src_date):
                    if not dry:
                        shutil.copytree(src_date, dst_date, dirs_exist_ok=True)
                        shutil.rmtree(src_date)
                    else:
                        print(f"  [DRY] {src_date} → {dst_date}")
                    # Track path mapping for DB update
                    mapping[f"{entry}/{date_dir}"] = f"lpr/{entry}/{date_dir}"
            if not dry and os.path.isdir(src_path) and not os.listdir(src_path):
                os.rmdir(src_path)
    return mapping


def migrate_face(dry: bool) -> dict:
    """
    /Detect/faces/{source_id}/ → /Detect/face/{source_id}/
    """
    mapping = {}
    src_root = os.path.join(DETECT_ROOT, "faces")
    dst_root = os.path.join(DETECT_ROOT, "face")
    if not os.path.isdir(src_root):
        print("faces/ không tồn tại, bỏ qua.")
        return mapping
    print(f"FACE: faces/ → face/")
    if not dry:
        if os.path.isdir(dst_root):
            # Merge
            for item in os.listdir(src_root):
                s = os.path.join(src_root, item)
                d = os.path.join(dst_root, item)
                shutil.copytree(s, d, dirs_exist_ok=True)
            shutil.rmtree(src_root)
        else:
            shutil.move(src_root, dst_root)
        print(f"  MOVED faces/ → face/")
    else:
        print(f"  [DRY] faces/ → face/")

    # DB path: "faces/..." → "face/..."
    mapping["faces/"] = "face/"
    return mapping


def migrate_behavior(dry: bool) -> dict:
    """
    /Detect/audit/{date}/{event_type}/{source_id}/ →
    /Detect/behavior/{source_id}/{date}/{event_type}/
    """
    mapping = {}
    src_root = os.path.join(DETECT_ROOT, "audit")
    dst_root = os.path.join(DETECT_ROOT, "behavior")
    if not os.path.isdir(src_root):
        print("audit/ không tồn tại, bỏ qua.")
        return mapping

    print("BEHAVIOR: audit/{date}/{event}/{source} → behavior/{source}/{date}/{event}")
    for date_dir in sorted(os.listdir(src_root)):
        date_path = os.path.join(src_root, date_dir)
        if not os.path.isdir(date_path):
            continue
        for event_type in sorted(os.listdir(date_path)):
            event_path = os.path.join(date_path, event_type)
            if not os.path.isdir(event_path):
                continue
            for source_id in sorted(os.listdir(event_path)):
                src = os.path.join(event_path, source_id)
                dst = os.path.join(dst_root, source_id, date_dir, event_type)
                if os.path.isdir(src):
                    if not dry:
                        os.makedirs(dst, exist_ok=True)
                        for f in os.listdir(src):
                            shutil.move(os.path.join(src, f), os.path.join(dst, f))
                    else:
                        print(f"  [DRY] audit/{date_dir}/{event_type}/{source_id}/ "
                              f"→ behavior/{source_id}/{date_dir}/{event_type}/")
                    # Track path mapping
                    old = os.path.join(DETECT_ROOT, "audit", date_dir, event_type, source_id)
                    new = os.path.join(DETECT_ROOT, "behavior", source_id, date_dir, event_type)
                    mapping[old] = new

    if not dry:
        # Xóa thư mục audit cũ (đã rỗng)
        shutil.rmtree(src_root, ignore_errors=True)

    return mapping


def update_db(lpr_map: dict, face_map: dict, behavior_map: dict) -> None:
    """
    Cập nhật image_path trong recognition_logs và json_path trong access_events.
    """
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 không có — bỏ qua DB update.")
        return

    dsn = os.environ.get(
        "DATABASE_URL",
        "host=localhost port=5433 dbname=svpro_db user=svpro_user password=svpro2024"
    )
    try:
        conn = psycopg2.connect(dsn)
        cur  = conn.cursor()
    except Exception as e:
        print(f"DB connect fail: {e}")
        return

    updated = 0

    # recognition_logs: image_path là relative path từ /Detect/
    # "faces/..." → "face/..."
    for old_prefix, new_prefix in face_map.items():
        cur.execute(
            """
            UPDATE recognition_logs
            SET metadata_json = jsonb_set(
                metadata_json,
                '{image_path}',
                to_jsonb(replace(metadata_json->>'image_path', %s, %s))
            )
            WHERE metadata_json->>'image_path' LIKE %s
            """,
            (old_prefix, new_prefix, f"{old_prefix}%")
        )
        updated += cur.rowcount
        print(f"  recognition_logs: updated {cur.rowcount} rows (faces→face)")

    # access_events: json_path là absolute path
    for old_path, new_path in behavior_map.items():
        cur.execute(
            "UPDATE access_events SET json_path = replace(json_path, %s, %s) WHERE json_path LIKE %s",
            (old_path, new_path, f"{old_path}%")
        )
        updated += cur.rowcount

    # Bulk replace /Detect/audit/ → /Detect/behavior/ trong json_path
    cur.execute(
        "UPDATE access_events SET json_path = replace(json_path, %s, %s) WHERE json_path LIKE %s",
        ("/Detect/audit/", "/Detect/behavior/", "/Detect/audit/%")
    )
    updated += cur.rowcount
    print(f"  access_events: json_path audit→behavior {cur.rowcount} rows")

    # Fix behavior path order trong access_events (date/event/source → source/date/event)
    # Phức tạp hơn — dùng Python loop trên từng record
    cur.execute(
        "SELECT id, json_path FROM access_events WHERE json_path LIKE '/Detect/behavior/%'"
    )
    rows = cur.fetchall()
    fixed = 0
    import re
    pattern = re.compile(
        r'/Detect/behavior/(\d{4}-\d{2}-\d{2})/([^/]+)/([^/]+)/(.*)'
    )
    for row_id, path in rows:
        if path is None:
            continue
        m = pattern.match(path)
        if m:
            date, event_type, source_id, rest = m.groups()
            new_path = f"/Detect/behavior/{source_id}/{date}/{event_type}/{rest}"
            cur.execute(
                "UPDATE access_events SET json_path = %s WHERE id = %s",
                (new_path, row_id)
            )
            fixed += 1
    if fixed:
        print(f"  access_events: reordered path for {fixed} rows")

    conn.commit()
    cur.close()
    conn.close()
    print(f"DB update done. Total rows affected: {updated + fixed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Chỉ in, không di chuyển")
    parser.add_argument("--update-db", action="store_true", help="Cập nhật paths trong PostgreSQL")
    args = parser.parse_args()

    dry = args.dry_run
    if dry:
        print("=== DRY RUN — không có gì bị thay đổi ===\n")

    print("=== Migrate LPR ===")
    lpr_map = migrate_lpr(dry)

    print("\n=== Migrate Face ===")
    face_map = migrate_face(dry)

    print("\n=== Migrate Behavior ===")
    behavior_map = migrate_behavior(dry)

    if args.update_db and not dry:
        print("\n=== Update DB paths ===")
        update_db(lpr_map, face_map, behavior_map)

    print("\nDone.")
