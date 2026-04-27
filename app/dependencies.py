"""FastAPI dependency providers."""

from __future__ import annotations

from arq import ArqRedis
from fastapi import Header, HTTPException

from app.config import settings


# Populated during app lifespan; read-only everywhere else
_redis_pool: ArqRedis | None = None


def set_redis_pool(pool: ArqRedis) -> None:
    global _redis_pool
    _redis_pool = pool


async def get_redis() -> ArqRedis:
    assert _redis_pool is not None, "Redis pool not initialised"
    return _redis_pool


def require_admin(x_admin_key: str = Header(...)) -> None:
    if x_admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
