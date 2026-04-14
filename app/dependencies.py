"""FastAPI dependency providers."""

from __future__ import annotations

from arq import ArqRedis


# Populated during app lifespan; read-only everywhere else
_redis_pool: ArqRedis | None = None


def set_redis_pool(pool: ArqRedis) -> None:
    global _redis_pool
    _redis_pool = pool


async def get_redis() -> ArqRedis:
    assert _redis_pool is not None, "Redis pool not initialised"
    return _redis_pool
