"""
Auth middleware và dependencies cho FastAPI backend SV-PRO.

2 cơ chế xác thực:
  1. API Key (X-API-Key header): Cho AI Core nội bộ gửi embedding/events.
  2. JWT Bearer token: Cho React Dashboard đăng nhập qua username/password.

JWT secret đọc từ env JWT_SECRET (bắt buộc trong production).
API key đọc từ env INTERNAL_API_KEY.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, APIKeyHeader
from pydantic import BaseModel

# ── Cấu hình ───────────────────────────────────────────────────────────────────
_JWT_SECRET    = os.environ.get("JWT_SECRET")
_JWT_ALGO      = "HS256"
_JWT_EXP_HOURS = 24
_API_KEY       = os.environ.get("INTERNAL_API_KEY", "svpro-internal-key")

if not _JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable must be set in production")

_bearer  = HTTPBearer(auto_error=False)
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False)

router = APIRouter()


# ── Pydantic models ─────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    expires_in:   int


# ── JWT helpers ─────────────────────────────────────────────────────────────────

def _create_token(username: str) -> str:
    """Tạo JWT token với claim sub=username và exp=24h."""
    import jwt  # lazy import để tránh circular dependency
    payload = {
        "sub": username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=_JWT_EXP_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGO)


def _verify_token(token: str) -> Optional[str]:
    """Giải mã JWT, trả về username nếu hợp lệ, None nếu không."""
    import jwt  # lazy import
    from jwt.exceptions import InvalidTokenError
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGO])
        return payload.get("sub")
    except InvalidTokenError:
        return None


# ── FastAPI Dependencies ────────────────────────────────────────────────────────

async def require_jwt(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> str:
    """
    Dependency: Yêu cầu JWT Bearer token hợp lệ.
    Dùng cho endpoints của Dashboard (cần đăng nhập).
    Trả về username nếu hợp lệ, raise 401 nếu không.
    """
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Cần xác thực JWT")
    username = _verify_token(credentials.credentials)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token không hợp lệ hoặc đã hết hạn")
    return username


async def require_api_key(key: Optional[str] = Security(_api_key)) -> str:
    """
    Dependency: Yêu cầu API Key hợp lệ trong header X-API-Key.
    Dùng cho endpoints nội bộ (AI Core gửi data).
    """
    if not key or key != _API_KEY:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API Key không hợp lệ")
    return key


# ── Login endpoint ──────────────────────────────────────────────────────────────

_ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
_ADMIN_PASS = os.environ.get("ADMIN_PASSWORD")  # Bắt buộc phải set trong .env


@router.post("/login", response_model=TokenResponse, summary="Đăng nhập Dashboard")
async def login(body: LoginRequest):
    """
    Đăng nhập bằng username/password, trả về JWT token.
    Frontend lưu token vào localStorage và gửi kèm mọi request tiếp theo.
    """
    if not _ADMIN_PASS:
        raise HTTPException(status_code=500, detail="ADMIN_PASSWORD chưa được cấu hình")
    if body.username != _ADMIN_USER or body.password != _ADMIN_PASS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sai username hoặc password")

    token = _create_token(body.username)
    return TokenResponse(
        access_token = token,
        expires_in   = _JWT_EXP_HOURS * 3600,
    )


@router.get("/me", summary="Thông tin người dùng hiện tại")
async def get_me(username: str = Depends(require_jwt)):
    """Trả về thông tin tài khoản đang đăng nhập."""
    return {"username": username, "role": "admin"}
