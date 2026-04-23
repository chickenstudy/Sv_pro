"""
Router Strangers — FastAPI backend SV-PRO.

Endpoints:
  GET /api/strangers               — Danh sách người lạ đã được track (kèm cameras_seen, last_image)
  GET /api/strangers/{uid}         — Chi tiết 1 stranger
  DELETE /api/strangers/{uid}      — Xóa stranger
  POST /api/strangers/{uid}/notes  — Thêm ghi chú cho stranger

Dữ liệu stranger lưu trong bảng guest_faces. AI Core (FaceRecognizer) UPSERT trực tiếp.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..database import get_db
from .auth import require_jwt

router = APIRouter()


# ── Pydantic schemas ────────────────────────────────────────────────────────────

class StrangerOut(BaseModel):
    """Thông tin một người lạ detected bởi FR pipeline."""
    stranger_id:    str
    source_id:       Optional[str] = None        # Camera đầu tiên thấy (legacy)
    first_seen:      Optional[str] = None
    last_seen:       Optional[str] = None
    quality_frames:   int = 0
    notes:            Optional[str] = None
    # ── Re-ID fields ─────────────────────────────────────────────────────────
    cameras_seen:     list[str] = []             # Tất cả camera đã thấy người này
    appearances:      int = 1                    # Số lần Re-ID match (gồm lần đầu)
    last_image_path:  Optional[str] = None       # Path ảnh mới nhất (relative /Detect)


class NoteIn(BaseModel):
    """Ghi chú của operator về stranger."""
    notes: str


_BASE_SELECT = """
    stranger_id,
    source_id,
    first_seen::text,
    last_seen::text,
    COALESCE(quality_frames, 0)                                   AS quality_frames,
    COALESCE(metadata_json->>'notes', '')                         AS notes,
    COALESCE(
        ARRAY(SELECT jsonb_array_elements_text(metadata_json->'cameras_seen')),
        ARRAY[]::text[]
    )                                                             AS cameras_seen,
    COALESCE((metadata_json->>'appearances')::int, 1)             AS appearances,
    metadata_json->>'last_image_path'                             AS last_image_path
"""


# ── Endpoints ───────────────────────────────────────────────────────────────────

@router.get("", response_model=list[StrangerOut], summary="Danh sách người lạ")
async def list_strangers(
    source_id: Optional[str] = None,
    limit:     int = 50,
    offset:    int = 0,
    db=Depends(get_db),
    _=Depends(require_jwt),
):
    """
    Trả về danh sách người lạ, sắp xếp theo lần xuất hiện gần nhất.
    Nếu cung cấp source_id, lọc theo cameras_seen chứa camera đó.
    """
    conditions: list[str] = []
    params: list = []

    if source_id:
        params.append(source_id)
        # cameras_seen JSONB array contains source_id
        conditions.append(
            f"COALESCE(metadata_json->'cameras_seen', '[]'::jsonb) @> "
            f"jsonb_build_array(${len(params)}::text)"
        )

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    rows = await db.fetch(
        f"""SELECT {_BASE_SELECT}
            FROM guest_faces {where}
            ORDER BY last_seen DESC
            LIMIT ${len(conditions)+1} OFFSET ${len(conditions)+2}""",
        *params,
    )
    return [dict(r) for r in rows]


@router.get("/{uid}", response_model=StrangerOut, summary="Chi tiết người lạ")
async def get_stranger(uid: str, db=Depends(get_db), _=Depends(require_jwt)):
    row = await db.fetchrow(
        f"""SELECT {_BASE_SELECT} FROM guest_faces WHERE stranger_id = $1""",
        uid,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Stranger không tồn tại")
    return dict(row)


class StrangerImage(BaseModel):
    """Một sự kiện nhận diện stranger có ảnh đính kèm."""
    image_path: str
    source_id:  Optional[str] = None
    created_at: str
    score:      Optional[float] = None


@router.get("/{uid}/images", response_model=list[StrangerImage],
            summary="Danh sách ảnh đã chụp của stranger")
async def list_stranger_images(
    uid: str,
    limit: int = 60,
    db=Depends(get_db),
    _=Depends(require_jwt),
):
    """
    Trả về tất cả ảnh đã được lưu cho stranger này (theo recognition_logs +
    metadata_json.image_path), sắp xếp mới → cũ. Bao gồm cả last_image_path
    trong guest_faces nếu chưa có trong logs.
    """
    rows = await db.fetch(
        """SELECT
              metadata_json->>'image_path' AS image_path,
              source_id,
              created_at::text             AS created_at,
              match_score                  AS score
           FROM recognition_logs
           WHERE person_id = $1
             AND is_stranger = TRUE
             AND metadata_json->>'image_path' IS NOT NULL
           ORDER BY created_at DESC
           LIMIT $2""",
        uid, limit,
    )
    images = [dict(r) for r in rows]

    # Fallback: nếu logs trống nhưng guest_faces có last_image_path thì trả về 1 entry
    if not images:
        gf = await db.fetchrow(
            """SELECT metadata_json->>'last_image_path' AS image_path,
                      source_id,
                      last_seen::text AS created_at
               FROM guest_faces WHERE stranger_id = $1""",
            uid,
        )
        if gf and gf["image_path"]:
            images = [{
                "image_path": gf["image_path"],
                "source_id":  gf["source_id"],
                "created_at": gf["created_at"],
                "score":      None,
            }]
    return images


@router.delete("/{uid}", status_code=204, summary="Xóa stranger")
async def delete_stranger(uid: str, db=Depends(get_db), _=Depends(require_jwt)):
    result = await db.execute("DELETE FROM guest_faces WHERE stranger_id = $1", uid)
    if result == 0:
        raise HTTPException(status_code=404, detail="Stranger không tồn tại")


class DedupResult(BaseModel):
    """Kết quả chạy dedup thủ công."""
    clusters_found:    int
    strangers_removed: int
    dry_run:           bool
    threshold:         float


@router.post("/dedup", response_model=DedupResult,
             summary="Chạy dedup ngay (gộp stranger trùng visual)")
async def run_dedup(
    apply: bool = False,
    threshold: float = 0.55,
    db=Depends(get_db),
    _=Depends(require_jwt),
):
    """
    Trigger dedup ngay lập tức từ FE. Cron đã chạy mỗi 1h, endpoint này dành
    cho operator muốn merge ngay sau khi quan sát thấy duplicate.

    apply=False (default): dry-run, chỉ trả số liệu.
    apply=True: thực sự merge.
    """
    # Inline implementation — không gọi script ngoài để tránh phụ thuộc subprocess
    rows = await db.fetch(
        """SELECT
              gf.stranger_id,
              COALESCE(gf.quality_frames, 0)         AS quality_frames,
              gf.face_embedding::text                AS centroid_str,
              COALESCE((
                  SELECT COUNT(*) FROM recognition_logs rl
                  WHERE rl.person_id = gf.stranger_id AND rl.is_stranger = TRUE
              ), 0)                                   AS log_count
           FROM guest_faces gf
           WHERE gf.face_embedding IS NOT NULL
             AND COALESCE(gf.quality_frames, 0) >= 3
           ORDER BY gf.last_seen DESC""",
    )
    if len(rows) < 2:
        return DedupResult(clusters_found=0, strangers_removed=0,
                           dry_run=not apply, threshold=threshold)

    multi = await db.fetch(
        "SELECT stranger_id, embedding::text AS emb_str FROM stranger_embeddings",
    )
    from collections import defaultdict
    embs_by_id: dict = defaultdict(list)
    for m in multi:
        embs_by_id[m["stranger_id"]].append(_parse_vec(m["emb_str"]))

    items = []
    for r in rows:
        sid = r["stranger_id"]
        embs = embs_by_id.get(sid) or [_parse_vec(r["centroid_str"])]
        items.append({
            "stranger_id":    sid,
            "quality_frames": r["quality_frames"],
            "log_count":      r["log_count"],
            "embeddings":     embs,
        })

    # Union-find clustering
    n = len(items)
    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for i in range(n):
        for j in range(i + 1, n):
            sim = max(
                sum(a*b for a, b in zip(ea, eb))
                for ea in items[i]["embeddings"]
                for eb in items[j]["embeddings"]
            )
            if sim >= threshold:
                ra, rb = find(i), find(j)
                if ra != rb: parent[ra] = rb

    groups: dict = defaultdict(list)
    for i, it in enumerate(items):
        groups[find(i)].append(it)
    clusters = [g for g in groups.values() if len(g) > 1]

    removed = 0
    for cluster in clusters:
        survivor = max(cluster, key=lambda s: (s["quality_frames"], s["log_count"]))
        losers = [s["stranger_id"] for s in cluster
                  if s["stranger_id"] != survivor["stranger_id"]]
        removed += len(losers)
        if not apply:
            continue
        await db.execute(
            "UPDATE recognition_logs SET person_id = $1 "
            "WHERE person_id = ANY($2::text[]) AND is_stranger = TRUE",
            survivor["stranger_id"], losers,
        )
        await db.execute(
            "UPDATE stranger_embeddings SET stranger_id = $1 "
            "WHERE stranger_id = ANY($2::text[])",
            survivor["stranger_id"], losers,
        )
        await db.execute(
            """DELETE FROM stranger_embeddings WHERE stranger_id = $1
               AND id NOT IN (SELECT id FROM stranger_embeddings
                              WHERE stranger_id = $1
                              ORDER BY quality DESC NULLS LAST LIMIT 5)""",
            survivor["stranger_id"],
        )
        await db.execute(
            "DELETE FROM guest_faces WHERE stranger_id = ANY($1::text[])",
            losers,
        )

    return DedupResult(clusters_found=len(clusters), strangers_removed=removed,
                       dry_run=not apply, threshold=threshold)


def _parse_vec(s: str) -> list[float]:
    """pgvector text format → list[float]"""
    return [float(x) for x in s.strip("[]").split(",")]


@router.post("/{uid}/notes", response_model=StrangerOut, summary="Thêm ghi chú")
async def add_notes(uid: str, body: NoteIn, db=Depends(get_db), _=Depends(require_jwt)):
    row = await db.fetchrow(
        f"""UPDATE guest_faces
           SET metadata_json = jsonb_set(COALESCE(metadata_json, '{{}}'), '{{notes}}', to_jsonb($2::text))
           WHERE stranger_id = $1
           RETURNING {_BASE_SELECT}""",
        uid, body.notes,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Stranger không tồn tại")
    return dict(row)
