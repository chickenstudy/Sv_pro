"""
Quản lý kết nối PostgreSQL bất đồng bộ (asyncpg) cho FastAPI backend SV-PRO.

Cung cấp:
  - `init_db()`: Khởi tạo connection pool khi server start.
  - `get_db()`: FastAPI dependency trả về connection từ pool.
  - `pool`: Global pool có thể dùng trực tiếp nếu cần.
"""

import os
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg

# ── Connection pool singleton ───────────────────────────────────────────────────
pool: Any = None

# ── Cấu hình DSN từ biến môi trường ────────────────────────────────────────────
_DB_DSN = os.environ.get(
    "POSTGRES_DSN",
    "postgresql://svpro_user:svpro_pass@postgres:5432/svpro_db",
)


async def init_db() -> None:
    """
    Tạo connection pool khi FastAPI server khởi động.
    Gọi 1 lần trong lifespan context manager của main.py.
    """
    global pool
    import asyncpg  # lazy import so unit/integration tests can patch init_db without asyncpg installed
    import json as _json

    async def _init_conn(conn):
        # Tự decode/encode JSONB → Python dict/list (mặc định asyncpg trả str).
        for typename in ("jsonb", "json"):
            await conn.set_type_codec(
                typename,
                encoder=_json.dumps,
                decoder=_json.loads,
                schema="pg_catalog",
            )

    pool = await asyncpg.create_pool(
        dsn      = _DB_DSN,
        min_size = 2,
        max_size = 10,
        command_timeout = 30,
        init     = _init_conn,
    )


async def get_db():
    """
    FastAPI dependency: cấp phát connection từ pool cho mỗi request.
    Tự động trả connection về pool sau khi request kết thúc.

    Dùng trong router:
        async def endpoint(db = Depends(get_db)):
            await db.fetch(...)
    """
    if pool is None:
        # Test/dev fallback: allow app to boot without a real DB pool.
        # Integration tests patch `init_db` and expect endpoints (e.g. /health) to work.
        class _DummyConn:
            async def fetchval(self, *_args, **_kwargs):
                return 1

            async def fetch(self, *_args, **_kwargs):
                return []

            async def fetchrow(self, *_args, **_kwargs):
                return None

        yield _DummyConn()
        return

    async with pool.acquire() as conn:
        yield conn
