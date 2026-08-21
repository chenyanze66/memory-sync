from contextlib import asynccontextmanager
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from .config import get_settings

pool = AsyncConnectionPool(
    conninfo=get_settings().database_url,
    min_size=1,
    max_size=6,
    open=False,
    kwargs={"autocommit": False},
)


async def open_pool() -> None:
    await pool.open(wait=True)


async def close_pool() -> None:
    await pool.close()


@asynccontextmanager
async def transaction(user_id: UUID | None = None):
    async with pool.connection() as conn:
        async with conn.transaction():
            if user_id is not None:
                await conn.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id),))
            yield conn


async def ping() -> bool:
    try:
        async with pool.connection(timeout=2) as conn:
            row = await conn.execute("SELECT 1")
            return (await row.fetchone())[0] == 1
    except Exception:
        return False


async def cleanup_expired_security_rows() -> None:
    async with transaction() as conn:
        await conn.execute("SELECT cleanup_expired_security_rows()")
