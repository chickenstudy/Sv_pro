"""
Face search by image — upload ảnh, tìm user / stranger có embedding gần nhất.

Workflow:
  1. FE upload ảnh.
  2. Backend gọi AI Core /internal/enroll (cùng SCRFD + ArcFace dùng cho realtime
     → embedding cùng không gian → match chính xác).
  3. Query pgvector trên cả `users` và `guest_faces` bằng cosine distance.
  4. Trả về top N (mặc định 5) match xếp theo similarity.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Query
from pydantic import BaseModel

from ..database import get_db
from .auth import require_jwt
from .enroll import _get_embedding_from_ai_core

router = APIRouter()
logger = logging.getLogger("face_search")


class MatchUserOut(BaseModel):
    type:        str            # "user" | "stranger"
    similarity:  float          # 0..1, càng cao càng giống
    distance:    float          # cosine distance pgvector trả về
    # user fields
    user_id:     Optional[int]    = None
    person_id:   Optional[str]    = None
    name:        Optional[str]    = None
    role:        Optional[str]    = None
    # stranger fields
    stranger_id: Optional[str]    = None
    last_image:  Optional[str]    = None
    cameras:     Optional[list[str]] = None


@router.post(
    "",
    response_model=list[MatchUserOut],
    summary="Tìm danh tính bằng ảnh khuôn mặt",
)
async def search_by_face(
    file:  UploadFile = File(..., description="Ảnh JPEG/PNG có 1 khuôn mặt"),
    limit: int = Query(5, ge=1, le=20),
    min_similarity: float = Query(0.40, ge=0.0, le=1.0),
    include_strangers: bool = Query(True),
    db=Depends(get_db),
    _=Depends(require_jwt),
):
    """
    POST 1 ảnh → trả về top `limit` match từ cả users và guest_faces, sort
    theo similarity giảm dần (chỉ lấy similarity ≥ min_similarity).

    Cosine distance pgvector (vector_cosine_ops): 0 = giống nhau hoàn toàn.
    Similarity = 1 - distance.
    """
    contents = await file.read()
    if len(contents) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Ảnh quá lớn (>15MB)")

    # Embed qua AI Core
    embedding = await _get_embedding_from_ai_core(contents, file.filename or "search.jpg")
    emb_str = "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"

    results: list[dict] = []

    # ── Search users ──────────────────────────────────────────────────────
    user_rows = await db.fetch(
        """SELECT id, person_id, name, role,
                  face_embedding <=> $1::vector AS distance
           FROM users
           WHERE active = TRUE AND face_embedding IS NOT NULL
           ORDER BY face_embedding <=> $1::vector
           LIMIT $2""",
        emb_str, limit,
    )
    for r in user_rows:
        sim = 1.0 - float(r["distance"])
        if sim >= min_similarity:
            results.append({
                "type":       "user",
                "similarity": round(sim, 4),
                "distance":   round(float(r["distance"]), 4),
                "user_id":    r["id"],
                "person_id":  r["person_id"],
                "name":       r["name"],
                "role":       r["role"],
            })

    # ── Search guest_faces (strangers) ────────────────────────────────────
    if include_strangers:
        stranger_rows = await db.fetch(
            """SELECT stranger_id,
                      metadata_json->>'last_image_path' AS last_image,
                      COALESCE(
                        ARRAY(SELECT jsonb_array_elements_text(metadata_json->'cameras_seen')),
                        ARRAY[]::text[]
                      ) AS cameras,
                      face_embedding <=> $1::vector AS distance
               FROM guest_faces
               WHERE face_embedding IS NOT NULL
               ORDER BY face_embedding <=> $1::vector
               LIMIT $2""",
            emb_str, limit,
        )
        for r in stranger_rows:
            sim = 1.0 - float(r["distance"])
            if sim >= min_similarity:
                results.append({
                    "type":        "stranger",
                    "similarity":  round(sim, 4),
                    "distance":    round(float(r["distance"]), 4),
                    "stranger_id": r["stranger_id"],
                    "last_image":  r["last_image"],
                    "cameras":     list(r["cameras"]) if r["cameras"] else [],
                })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:limit]
