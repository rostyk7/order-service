"""Shared pytest fixtures."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import get_redis
from app.main import app
from app.models import Order, OrderStatus

import uuid

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine():
    # StaticPool: all connections share one underlying SQLite connection
    # so the in-memory DB persists for the whole test session
    _engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield _engine
    await _engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(engine):
    """Truncate all tables before each test — prevents data leaking between tests."""
    yield
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.enqueue_job = AsyncMock(return_value=None)
    return redis


@pytest_asyncio.fixture
async def client(engine, mock_redis) -> AsyncGenerator[AsyncClient, None]:
    """HTTP test client wired to the shared in-memory DB and mock Redis."""
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_redis] = lambda: mock_redis

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def sample_order() -> Order:
    return Order(
        id=uuid.uuid4(),
        customer_email="test@example.com",
        amount=5000,
        currency="USD",
        status=OrderStatus.PENDING,
        card_token="tok_success",
        metadata_={},
    )
