"""FastAPI application entry-point."""

import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI

from app.config import settings
from app.dependencies import set_redis_pool
from app.routers import orders, webhooks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── startup ──────────────────────────────────────────────────────────────
    redis = await create_pool(
        RedisSettings(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
    )
    set_redis_pool(redis)
    yield
    # ── shutdown ─────────────────────────────────────────────────────────────
    await redis.close()


app = FastAPI(
    title="Order Service",
    description="Event-driven order processing backed by payment-provider",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(orders.router)
app.include_router(webhooks.router)


@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok"}
