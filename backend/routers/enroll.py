"""
Enroll Router — SV-PRO Face Enrollment (Proxy sang AI Core).

Luồng đúng kiến trúc:
  1. Dashboard upload ảnh lên đây (POST /api/enroll/{id}/face).
  2. Backend proxy ảnh sang AI Core Enrollment Service (POST /internal/enroll).
  3. AI Core dùng SCRFD + ArcFace đã warmup sẵn → trả embedding 512-dim.
  4. Backend lưu embedding vào DB + invalidate Redis cache.

Lý do KHÔNG tự load model ở backend:
  - Model đã được load + TensorRT-optimized trong AI Core → tái dụng là đúng.
  - CPU ONNX embedding ≠ GPU TensorRT embedding → nếu tự load sẽ gây false negative.
  - Backend phải là stateless thin API, không ôm GPU inference.

Endpoints:
  POST /api/enroll/{user_id}/face    — Upload 1 ảnh → embed → lưu DB
  POST /api/enroll/{user_id}/faces   — Upload nhiều ảnh → embedding trung bình
  DELETE /api/enroll/{user_id}/face  — Xóa embedding
  GET  /api/enroll/status            — Kiểm tra AI Core enrollment service
"""

import logging
import os

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..database import get_db
from .auth import require_jwt

router = APIRouter()
logger = logging.getLogger("enroll")

# ── Config — AI Core Enrollment Service ──────────────────────────────────────
_AI_CORE_ENROLL_URL = os.environ.get(
    "AI_CORE_ENROLL_URL", "http://savant-ai-core:8090"
)
_ENROLL_ENDPOINT    = f"{_AI_CORE_ENROLL_URL}/internal/enroll"
_HEALTH_ENDPOINT    = f"{_AI_CORE_ENROLL_URL}/internal/health"
_RELOAD_ENDPOINT    = f"{_AI_CORE_ENROLL_URL}/internal/reload-embeddings"
_TIMEOUT_SEC        = 30.0   # Enroll có thể chậm hơn bình thường (TensorRT warmup)

# HTTP client dùng chung (connection pooling)
_http_client = httpx.AsyncClient(timeout=_TIMEOUT_SEC)


async def _trigger_reload_embeddings() -> None:
    """
    Gọi AI core reload Redis hash + L1 cache sau khi enroll/update.
    Fire-and-forget — nếu fail (AI core down) cũng không chặn enrollment;
    Redis TTL 5 phút sẽ tự reload sau cùng.
    """
    try:
        await _http_client.post(_RELOAD_ENDPOINT, timeout=3.0)
    except Exception as exc:
        logger.warning("Reload embeddings call failed (will fallback to TTL): %s", exc)


# ── Helper: gọi AI Core để lấy embedding ─────────────────────────────────────

async def _get_embedding_from_ai_core(image_bytes: bytes, filename: str) -> list[float]:
    """
    Gửi ảnh sang AI Core Enrollment Service và nhận embedding 512-dim về.
    Raise HTTPException nếu AI Core không khả dụng hoặc không detect được mặt.
    """
    try:
        resp = await _http_client.post(
            _ENROLL_ENDPOINT,
            files={"image": (filename, image_bytes, "image/jpeg")},
        )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=(
                "AI Core Enrollment Service không khả dụng. "
                "Kiểm tra savant-ai-core đang chạy và SCRFD/ArcFace đã load xong."
            ),
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="AI Core hết thời gian phản hồi. TensorRT có thể đang warmup — thử lại sau 30 giây.",
        )

    data = resp.json()

    if resp.status_code == 422:
        raise HTTPException(status_code=422, detail=data.get("error", "Không phát hiện khuôn mặt"))
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"AI Core trả lỗi {resp.status_code}: {data.get('error', resp.text)}",
        )

    embedding = data.get("embedding")
    if not embedding or len(embedding) != 512:
        raise HTTPException(status_code=502, detail="AI Core trả embedding không hợp lệ (cần 512 chiều)")

    return embedding


async def _save_embedding(user_id: int, embedding: list[float], db) -> None:
    """Lưu embedding 512-dim vào bảng users dưới dạng pgvector."""
    emb_str = "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"
    await db.execute(
        "UPDATE users SET face_embedding=$1::vector, embedding_version=embedding_version+1 WHERE id=$2",
        emb_str, user_id,
    )


def _invalidate_redis(user_id: int) -> None:
    """
    Xóa embedding người dùng khỏi Redis cache để AI Core đọc embedding mới ngay.
    Best-effort: không raise nếu Redis không khả dụng.
    """
    try:
        import redis as redis_lib
        r = redis_lib.Redis(
            host=os.environ.get("REDIS_HOST", "redis"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            socket_connect_timeout=2,
        )
        r.hdel("svpro:staff:hash", user_id)          # Staff embedding cache
        r.delete(f"svpro:bl:person:{user_id}")        # Blacklist cache
        logger.info("Redis cache invalidated for user_id=%s", user_id)
    except Exception as exc:
        logger.debug("Redis invalidate failed (non-critical): %s", exc)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/{user_id}/face",
    status_code=200,
    summary="Đăng ký khuôn mặt từ 1 ảnh upload",
    description="""
Upload 1 ảnh khuôn mặt. Backend proxy sang AI Core để extract embedding bằng
SCRFD + ArcFace đã warmup sẵn (cùng model với inference realtime → match chính xác).

**Yêu cầu ảnh:** JPEG/PNG, 1 khuôn mặt rõ ràng nhìn thẳng, ánh sáng đủ.
""",
)
async def enroll_face_from_image(
    user_id: int,
    file: UploadFile = File(..., description="Ảnh khuôn mặt (JPEG/PNG, tối đa 15MB)"),
    db=Depends(get_db),
    _=Depends(require_jwt),
):
    """Nhận ảnh → proxy sang AI Core → embed → lưu DB → invalidate Redis."""
    row = await db.fetchrow("SELECT id, name FROM users WHERE id=$1 AND active=TRUE", user_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Người dùng {user_id} không tồn tại")

    contents = await file.read()
    if len(contents) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Ảnh quá lớn (tối đa 15MB)")

    embedding = await _get_embedding_from_ai_core(contents, file.filename or "image.jpg")
    await _save_embedding(user_id, embedding, db)
    _invalidate_redis(user_id)
    await _trigger_reload_embeddings()   # AI core re-prefetch + clear L1 → match liền

    logger.info("Face enrolled for user_id=%s name=%s via AI Core", user_id, row["name"])
    return {
        "success":  True,
        "user_id":  user_id,
        "name":     row["name"],
        "message":  f"Đã đăng ký khuôn mặt thành công cho {row['name']}. AI Core nhận diện ngay.",
        "tip":      "Dùng endpoint /faces để upload nhiều ảnh cho độ chính xác cao hơn.",
    }


@router.post(
    "/{user_id}/faces",
    status_code=200,
    summary="Đăng ký khuôn mặt từ nhiều ảnh (chính xác hơn)",
    description="""
Upload 2–10 ảnh từ nhiều góc/ánh sáng. Backend tính embedding trung bình
từ tất cả ảnh → profile ổn định, ít bị ảnh hưởng bởi điều kiện ánh sáng.
""",
)
async def enroll_faces_multi(
    user_id: int,
    files: list[UploadFile] = File(..., description="Danh sách 1–10 ảnh khuôn mặt"),
    db=Depends(get_db),
    _=Depends(require_jwt),
):
    """Upload nhiều ảnh → embedding trung bình → lưu DB."""
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Tối đa 10 ảnh mỗi lần đăng ký")

    row = await db.fetchrow("SELECT id, name FROM users WHERE id=$1 AND active=TRUE", user_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Người dùng {user_id} không tồn tại")

    import numpy as np
    embeddings, failed = [], []

    for f in files:
        try:
            contents = await f.read()
            if len(contents) > 15 * 1024 * 1024:
                failed.append({"file": f.filename, "reason": "Quá lớn (>15MB)"})
                continue
            emb = await _get_embedding_from_ai_core(contents, f.filename or "image.jpg")
            embeddings.append(emb)
        except HTTPException as e:
            failed.append({"file": f.filename, "reason": e.detail})
        except Exception as exc:
            failed.append({"file": f.filename, "reason": str(exc)})

    if not embeddings:
        raise HTTPException(
            status_code=422,
            detail={"message": "Không có ảnh nào xử lý được", "failed": failed},
        )

    # Tính embedding trung bình, chuẩn hóa L2
    mean_emb = np.array(embeddings, dtype=np.float32).mean(axis=0)
    mean_emb = mean_emb / (np.linalg.norm(mean_emb) + 1e-6)

    await _save_embedding(user_id, mean_emb.tolist(), db)
    _invalidate_redis(user_id)
    await _trigger_reload_embeddings()   # AI core re-prefetch + clear L1 → match liền

    return {
        "success":        True,
        "user_id":        user_id,
        "name":           row["name"],
        "images_ok":      len(embeddings),
        "images_failed":  len(failed),
        "failed_details": failed,
        "message":        f"Đã đăng ký từ {len(embeddings)}/{len(files)} ảnh thành công.",
    }


@router.delete(
    "/{user_id}/face",
    status_code=200,
    summary="Xóa dữ liệu khuôn mặt",
)
async def delete_face_enrollment(
    user_id: int,
    db=Depends(get_db),
    _=Depends(require_jwt),
):
    """Xóa embedding → người này sẽ bị nhận là stranger cho đến khi đăng ký lại."""
    row = await db.fetchrow("SELECT id, name FROM users WHERE id=$1", user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Người dùng không tồn tại")

    result = await db.execute(
        "UPDATE users SET face_embedding=NULL WHERE id=$1 AND face_embedding IS NOT NULL",
        user_id,
    )
    if result == "UPDATE 0":
        return {"success": False, "message": "Người dùng này chưa có dữ liệu khuôn mặt"}

    _invalidate_redis(user_id)
    await _trigger_reload_embeddings()   # AI core re-prefetch sau khi xóa
    return {"success": True, "user_id": user_id, "name": row["name"],
            "message": f"Đã xóa dữ liệu khuôn mặt của {row['name']}."}


@router.get(
    "/status",
    summary="Trạng thái AI Core Enrollment Service",
)
async def enrollment_status(_=Depends(require_jwt)):
    """Kiểm tra AI Core enrollment HTTP service có sẵn sàng nhận request không."""
    try:
        resp = await _http_client.get(_HEALTH_ENDPOINT, timeout=5.0)
        ai_core_ok = resp.status_code == 200
        ai_core_msg = resp.json().get("status", "unknown") if ai_core_ok else f"HTTP {resp.status_code}"
    except Exception as exc:
        ai_core_ok  = False
        ai_core_msg = str(exc)

    return {
        "ready":           ai_core_ok,
        "ai_core_url":     _AI_CORE_ENROLL_URL,
        "ai_core_status":  ai_core_msg,
        "note":            "AI Core cần chạy và SCRFD/ArcFace phải load xong (~60-120s sau khi start)",
    }
