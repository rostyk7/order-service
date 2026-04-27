"""E2e test helpers — async utilities that operate on the running API."""

from __future__ import annotations

import asyncio

import httpx

from tests.e2e.constants import E2E_TIMEOUT


async def poll_for_status(
    client: httpx.AsyncClient,
    order_id: str,
    expected: str,
    timeout: int = E2E_TIMEOUT,
) -> dict:
    """Poll GET /orders/{id} until status matches or timeout expires."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/orders/{order_id}")
        resp.raise_for_status()
        data = resp.json()
        if data["status"] == expected:
            return data
        await asyncio.sleep(0.5)
    raise TimeoutError(
        f"Order {order_id} did not reach {expected!r} within {timeout}s. "
        f"Last status: {data['status']!r}"
    )
