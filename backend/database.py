"""
Quản lý kết nối PostgreSQL bất đồng bộ (asyncpg) cho FastAPI backend SV-PRO.

Cung cấp:
  - `init_db()`: Khởi tạo connection pool khi server start.
  - `get_db()`: FastAPI dependency trả về connection từ pool.
  - `pool`: Global pool có thể dùng trực tiếp nếu cần.
"""

import os
import asyncpg

# ── Connection pool singleton ───────────────────────────────────────────────────
pool: asyncpg.Pool | None = None

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
    pool = await asyncpg.create_pool(
        dsn      = _DB_DSN,
        min_size = 2,
        max_size = 10,
        command_timeout = 30,
    )


async def get_db():
    """
    FastAPI dependency: cấp phát connection từ pool cho mỗi request.
    Tự động trả connection về pool sau khi request kết thúc.

    Dùng trong router:
        async def endpoint(db = Depends(get_db)):
            await db.fetch(...)
    """
    async with pool.acquire() as conn:
        yield conn
