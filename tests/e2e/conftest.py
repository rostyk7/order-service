"""Shared pytest fixtures for e2e tests."""

from __future__ import annotations

import httpx
import pytest_asyncio

from tests.e2e.constants import API_BASE_URL


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0) as c:
        yield c
