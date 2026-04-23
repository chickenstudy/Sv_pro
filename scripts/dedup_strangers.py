#!/usr/bin/env python3
"""
Stranger Dedup — gom các stranger_id trùng visual về 1 ID đại diện.

Lý do tồn tại:
  Pipeline FR có thể tạo ra nhiều stranger_id cho CÙNG 1 người vì:
    - Track NvSORT bị reset (occlusion / ra khỏi frame > 8s) → track mới
    - Re-ID online dùng threshold 0.40 — đôi khi miss khi góc/sáng quá khác
    - Camera khác nhau, đèn khác nhau → embedding xa hơn 0.40

  Script này chạy offline, dùng threshold cao hơn (0.55 cosine sim) để
  conservative gộp các stranger có VISUAL IDENTITY trùng. Chỉ gộp khi rất chắc.

Thuật toán:
  1. Load tất cả guest_faces.face_embedding (centroid) + multi-emb từ stranger_embeddings.
  2. Build similarity graph: edge giữa 2 stranger nếu best-pair cosine sim ≥ THRESHOLD.
  3. Connected components → mỗi component = 1 cluster trùng nhau.
  4. Cluster size > 1 → chọn survivor (quality_frames cao nhất, tie-break: nhiều log nhất).
  5. UPDATE recognition_logs.person_id của các stranger trong cluster về survivor.
  6. DELETE FROM guest_faces các stranger không phải survivor (CASCADE xóa embeddings).

Default DRY-RUN: chỉ in plan. Truyền --apply để thực sự thay đổi DB.

Cron:
  Chạy hourly từ container backend: `python -m scripts.dedup_strangers --apply`
  Lock file đảm bảo không 2 instance chạy song song.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from typing import Dict, List, Set, Tuple

import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] dedup-strangers: %(message)s",
)
log = logging.getLogger(__name__)

DB_DSN = os.environ.get("POSTGRES_DSN",
                        "postgresql://svpro_user:svpro_pass@postgres:5432/svpro_db")

# Cosine similarity threshold để coi 2 stranger là TRÙNG.
# Thấp = gộp aggressive (rủi ro merge nhầm 2 người khác)
# Cao = conservative (an toàn nhưng để sót duplicate)
# 0.55: cao hơn nhiều threshold online (0.40) → chỉ gộp khi RẤT chắc.
DEDUP_SIM_THRESHOLD = float(os.environ.get("DEDUP_SIM_THRESHOLD", "0.55"))

# Lock file path để tránh chạy chồng (cron có thể trigger 2 lần khi script chậm)
LOCK_PATH = "/tmp/svpro_dedup_strangers.lock"

# Stranger phải có quality_frames tối thiểu để tham gia dedup
# (tránh stranger noise quá ngắn được gộp lung tung)
MIN_QUALITY_FRAMES = 3


def _try_acquire_lock() -> bool:
    """Đơn giản: check file tồn tại + age. Nếu lock cũ > 1h, override."""
    if os.path.exists(LOCK_PATH):
        age = time.time() - os.path.getmtime(LOCK_PATH)
        if age < 3600:
            log.warning("Lock file %s tồn tại (age=%.0fs) — bỏ qua run này.", LOCK_PATH, age)
            return False
        log.warning("Lock file cũ %.0fs > 1h — override.", age)
    with open(LOCK_PATH, "w") as fh:
        fh.write(f"pid={os.getpid()} ts={int(time.time())}\n")
    return True


def _release_lock():
    try:
        os.remove(LOCK_PATH)
    except FileNotFoundError:
        pass


def fetch_strangers(conn) -> List[Dict]:
    """
    Trả list dict gồm: stranger_id, quality_frames, log_count, centroid (list[float]),
    embeddings (list of list[float] — multi-emb từ stranger_embeddings).
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Centroid + log count + multi-emb dạng aggregated
    cur.execute("""
        SELECT
            gf.stranger_id,
            COALESCE(gf.quality_frames, 0)               AS quality_frames,
            gf.face_embedding::text                       AS centroid_str,
            COALESCE((
                SELECT COUNT(*) FROM recognition_logs rl
                WHERE rl.person_id = gf.stranger_id AND rl.is_stranger = TRUE
            ), 0)                                         AS log_count
        FROM guest_faces gf
        WHERE gf.face_embedding IS NOT NULL
          AND COALESCE(gf.quality_frames, 0) >= %s
        ORDER BY gf.last_seen DESC
    """, (MIN_QUALITY_FRAMES,))
    centroids_rows = cur.fetchall()

    # Multi-embeddings (gallery)
    cur.execute("""
        SELECT stranger_id, embedding::text AS emb_str, quality
        FROM stranger_embeddings
        ORDER BY stranger_id, quality DESC
    """)
    multi_rows = cur.fetchall()
    cur.close()

    multi_by_id: Dict[str, list] = defaultdict(list)
    for r in multi_rows:
        multi_by_id[r["stranger_id"]].append(_parse_vector(r["emb_str"]))

    out = []
    for r in centroids_rows:
        sid = r["stranger_id"]
        centroid = _parse_vector(r["centroid_str"])
        embs = multi_by_id.get(sid, [])
        if not embs:
            embs = [centroid]   # fallback dùng centroid nếu chưa có gallery
        out.append({
            "stranger_id":    sid,
            "quality_frames": r["quality_frames"],
            "log_count":      r["log_count"],
            "centroid":       centroid,
            "embeddings":     embs,
        })
    return out


def _parse_vector(s: str) -> List[float]:
    """pgvector text format: '[0.12,0.34,...]' → list[float]"""
    s = s.strip("[]")
    return [float(x) for x in s.split(",")]


def _cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity giữa 2 vector — embeddings đã L2-normalized → dot product."""
    return sum(x * y for x, y in zip(a, b))


def _best_pair_sim(embs_a: List[List[float]], embs_b: List[List[float]]) -> float:
    """Max cosine sim giữa mọi cặp embedding của 2 stranger."""
    best = 0.0
    for ea in embs_a:
        for eb in embs_b:
            s = _cosine(ea, eb)
            if s > best:
                best = s
    return best


def find_clusters(strangers: List[Dict], threshold: float) -> List[List[Dict]]:
    """
    Union-find clustering dựa trên best-pair sim ≥ threshold.
    Trả list của clusters; mỗi cluster là list các stranger dict.
    """
    n = len(strangers)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    edges_count = 0
    for i in range(n):
        for j in range(i + 1, n):
            sim = _best_pair_sim(strangers[i]["embeddings"], strangers[j]["embeddings"])
            if sim >= threshold:
                union(i, j)
                edges_count += 1

    log.info("Built similarity graph: %d nodes, %d edges (sim ≥ %.2f)",
             n, edges_count, threshold)

    groups: Dict[int, List[Dict]] = defaultdict(list)
    for i, s in enumerate(strangers):
        groups[find(i)].append(s)

    clusters = [g for g in groups.values() if len(g) > 1]
    return clusters


def pick_survivor(cluster: List[Dict]) -> Dict:
    """Chọn stranger giữ lại: ưu tiên quality_frames cao nhất, tie-break log_count cao nhất."""
    return max(cluster, key=lambda s: (s["quality_frames"], s["log_count"]))


def apply_merge(conn, clusters: List[List[Dict]], dry_run: bool) -> Tuple[int, int]:
    """
    Thực hiện merge mỗi cluster về 1 survivor.
    Trả về (clusters_processed, total_strangers_removed).
    """
    cur = conn.cursor()
    total_removed = 0

    for cluster in clusters:
        survivor = pick_survivor(cluster)
        losers = [s for s in cluster if s["stranger_id"] != survivor["stranger_id"]]
        loser_ids = [s["stranger_id"] for s in losers]

        log.info(
            "Cluster size=%d → survivor=%s (qf=%d logs=%d) | merge: %s",
            len(cluster), survivor["stranger_id"],
            survivor["quality_frames"], survivor["log_count"],
            ", ".join(f"{s['stranger_id']}(qf={s['quality_frames']},logs={s['log_count']})"
                      for s in losers),
        )

        if dry_run:
            total_removed += len(loser_ids)
            continue

        # 1. Re-point recognition_logs
        cur.execute(
            "UPDATE recognition_logs SET person_id = %s "
            "WHERE person_id = ANY(%s) AND is_stranger = TRUE",
            (survivor["stranger_id"], loser_ids),
        )
        rl_updated = cur.rowcount

        # 2. Tăng quality_frames + cập nhật last_seen của survivor
        cur.execute(
            """UPDATE guest_faces
               SET quality_frames = quality_frames + (
                   SELECT COALESCE(SUM(quality_frames), 0) FROM guest_faces
                   WHERE stranger_id = ANY(%s)
               ),
               last_seen = GREATEST(last_seen, (
                   SELECT MAX(last_seen) FROM guest_faces WHERE stranger_id = ANY(%s)
               )),
               metadata_json = jsonb_set(
                   COALESCE(metadata_json, '{}'),
                   '{merged_from}',
                   COALESCE(metadata_json->'merged_from', '[]'::jsonb) ||
                       to_jsonb(%s::text[])
               )
               WHERE stranger_id = %s""",
            (loser_ids, loser_ids, loser_ids, survivor["stranger_id"]),
        )

        # 3. Move multi-embeddings từ losers → survivor (giữ tối đa K=5 quality cao nhất)
        cur.execute(
            "UPDATE stranger_embeddings SET stranger_id = %s WHERE stranger_id = ANY(%s)",
            (survivor["stranger_id"], loser_ids),
        )
        # Giữ K=5 embedding chất lượng cao nhất cho survivor
        cur.execute(
            """DELETE FROM stranger_embeddings
               WHERE stranger_id = %s AND id NOT IN (
                   SELECT id FROM stranger_embeddings
                   WHERE stranger_id = %s
                   ORDER BY quality DESC NULLS LAST
                   LIMIT 5
               )""",
            (survivor["stranger_id"], survivor["stranger_id"]),
        )

        # 4. DELETE losers — trigger trg_cleanup_se sẽ tự xoá embeddings còn lại của losers
        cur.execute(
            "DELETE FROM guest_faces WHERE stranger_id = ANY(%s)",
            (loser_ids,),
        )
        gf_deleted = cur.rowcount
        total_removed += gf_deleted

        log.info("  ↳ logs re-pointed: %d, guest_faces deleted: %d",
                 rl_updated, gf_deleted)

    if not dry_run:
        conn.commit()
    cur.close()
    return len(clusters), total_removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Thực sự ghi DB. Mặc định DRY-RUN chỉ in plan.")
    parser.add_argument("--threshold", type=float, default=DEDUP_SIM_THRESHOLD,
                        help=f"Cosine similarity threshold (default {DEDUP_SIM_THRESHOLD})")
    parser.add_argument("--no-lock", action="store_true",
                        help="Bỏ qua check lock file (debug only)")
    args = parser.parse_args()

    if not args.no_lock and not _try_acquire_lock():
        sys.exit(0)

    try:
        log.info("Connecting %s", DB_DSN.split("@")[-1])
        conn = psycopg2.connect(DB_DSN)

        log.info("Loading strangers (quality_frames ≥ %d) ...", MIN_QUALITY_FRAMES)
        strangers = fetch_strangers(conn)
        log.info("Loaded %d strangers eligible for dedup", len(strangers))

        if len(strangers) < 2:
            log.info("Không đủ stranger để dedup — exit.")
            return

        clusters = find_clusters(strangers, args.threshold)
        log.info("Found %d duplicate cluster(s)", len(clusters))

        if not clusters:
            log.info("Không có cluster trùng — DB sạch.")
            return

        n_clusters, n_removed = apply_merge(conn, clusters, dry_run=not args.apply)

        mode = "APPLIED" if args.apply else "DRY-RUN"
        log.info("[%s] Merged %d cluster(s) → %d stranger(s) removed",
                 mode, n_clusters, n_removed)

        if not args.apply:
            log.info("Re-run với --apply để thực sự thay đổi DB.")

        conn.close()
    finally:
        if not args.no_lock:
            _release_lock()


if __name__ == "__main__":
    main()
