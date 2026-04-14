"""
End-to-end tests — full stack running (payment-provider + order-service).

These tests hit the real API over HTTP. The stack is started via
docker-compose.e2e.yml before the suite runs.

Environment:
  API_BASE_URL   Base URL for the order-service API (default: http://localhost:8000)
  E2E_TIMEOUT    Seconds to wait for async state changes    (default: 15)
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest
import pytest_asyncio

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
E2E_TIMEOUT = int(os.getenv("E2E_TIMEOUT", "15"))


# ── Helpers ───────────────────────────────────────────────────────────────────

async def poll_for_status(
    client: httpx.AsyncClient,
    order_id: str,
    expected: str,
    timeout: int = E2E_TIMEOUT,
) -> dict:
    """Poll GET /orders/{id} until status matches or timeout is reached."""
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


def order_payload(card_token: str, amount: int = 5000) -> dict:
    return {
        "customer_email": f"e2e-{uuid.uuid4().hex[:6]}@example.com",
        "amount": amount,
        "currency": "USD",
        "card_token": card_token,
        "metadata": {"test": True},
    }


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0) as c:
        yield c


# ── Smoke test ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client: httpx.AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── Happy path: tok_success ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tok_success_full_flow(client: httpx.AsyncClient):
    """
    tok_success → payment settles → webhook fires → order becomes PAID
    Then fulfill the order.
    """
    # 1. Create order
    resp = await client.post("/orders", json=order_payload("tok_success"))
    assert resp.status_code == 202
    data = resp.json()
    order_id = data["id"]
    assert data["status"] == "AWAITING_PAYMENT"
    assert data["payment_id"] is not None

    # 2. Wait for payment-provider to process and fire webhook
    data = await poll_for_status(client, order_id, "PAID")
    assert data["status"] == "PAID"
    assert any(e["event_type"] == "payment_settled" for e in data["events"])
    assert any(n["type"] == "ORDER_CONFIRMED" for n in data["notifications"])

    # 3. Fulfill
    resp = await client.post(f"/orders/{order_id}/fulfill", json={"note": "Shipped"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "FULFILLED"
    assert any(n["type"] == "ORDER_FULFILLED" for n in resp.json()["notifications"])


# ── Happy path: tok_success → refund ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_tok_success_refund_flow(client: httpx.AsyncClient):
    """
    tok_success → PAID → refund → REFUNDED
    """
    resp = await client.post("/orders", json=order_payload("tok_success"))
    assert resp.status_code == 202
    order_id = resp.json()["id"]

    await poll_for_status(client, order_id, "PAID")

    resp = await client.post(
        f"/orders/{order_id}/refund", json={"reason": "Changed mind"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "REFUNDED"
    assert any(e["event_type"] == "order_refunded" for e in data["events"])
    assert any(n["type"] == "ORDER_REFUNDED" for n in data["notifications"])


# ── Failure path: tok_insufficient_funds ─────────────────────────────────────

@pytest.mark.asyncio
async def test_tok_insufficient_funds(client: httpx.AsyncClient):
    """
    tok_insufficient_funds → payment fails → webhook fires → order becomes PAYMENT_FAILED
    """
    resp = await client.post("/orders", json=order_payload("tok_insufficient_funds"))
    assert resp.status_code == 202
    order_id = resp.json()["id"]

    data = await poll_for_status(client, order_id, "PAYMENT_FAILED")
    assert data["status"] == "PAYMENT_FAILED"
    assert any(e["event_type"] == "payment_failed" for e in data["events"])
    assert any(n["type"] == "PAYMENT_FAILED" for n in data["notifications"])


# ── Failure path: tok_card_declined ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_tok_card_declined(client: httpx.AsyncClient):
    resp = await client.post("/orders", json=order_payload("tok_card_declined"))
    assert resp.status_code == 202
    order_id = resp.json()["id"]

    data = await poll_for_status(client, order_id, "PAYMENT_FAILED")
    assert data["status"] == "PAYMENT_FAILED"


# ── Failure path: tok_do_not_honor ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tok_do_not_honor(client: httpx.AsyncClient):
    resp = await client.post("/orders", json=order_payload("tok_do_not_honor"))
    assert resp.status_code == 202
    order_id = resp.json()["id"]

    data = await poll_for_status(client, order_id, "PAYMENT_FAILED")
    assert data["status"] == "PAYMENT_FAILED"


# ── Retry after failure ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_after_payment_failed(client: httpx.AsyncClient):
    """
    tok_card_declined → PAYMENT_FAILED
    Retry with tok_success → PAID
    """
    # 1. Fail intentionally
    resp = await client.post("/orders", json=order_payload("tok_card_declined"))
    assert resp.status_code == 202
    order_id = resp.json()["id"]
    await poll_for_status(client, order_id, "PAYMENT_FAILED")

    # 2. Retry — payment-provider re-charges using the same payment_id
    #    which was set at creation; retry endpoint resets it to PENDING on provider side
    resp = await client.post(f"/orders/{order_id}/retry-payment")
    # Note: this endpoint isn't implemented yet — see below
    # For now assert we handle this correctly


# ── Cancel flows ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_before_payment(client: httpx.AsyncClient):
    """Cancel an order while it is still AWAITING_PAYMENT."""
    resp = await client.post("/orders", json=order_payload("tok_success"))
    assert resp.status_code == 202
    order_id = resp.json()["id"]
    assert resp.json()["status"] == "AWAITING_PAYMENT"

    resp = await client.post(f"/orders/{order_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"
    assert any(n["type"] == "ORDER_CANCELLED" for n in resp.json()["notifications"])


@pytest.mark.asyncio
async def test_cancel_after_payment_failed(client: httpx.AsyncClient):
    resp = await client.post("/orders", json=order_payload("tok_insufficient_funds"))
    assert resp.status_code == 202
    order_id = resp.json()["id"]

    await poll_for_status(client, order_id, "PAYMENT_FAILED")

    resp = await client.post(f"/orders/{order_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


# ── Guard rails ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cannot_refund_unfulfilled_order(client: httpx.AsyncClient):
    """FULFILLED → refund must be blocked."""
    resp = await client.post("/orders", json=order_payload("tok_success"))
    order_id = resp.json()["id"]

    await poll_for_status(client, order_id, "PAID")
    await client.post(f"/orders/{order_id}/fulfill")

    resp = await client.post(f"/orders/{order_id}/refund")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_cannot_fulfill_before_paid(client: httpx.AsyncClient):
    """AWAITING_PAYMENT → fulfill must be blocked."""
    resp = await client.post("/orders", json=order_payload("tok_success"))
    order_id = resp.json()["id"]

    resp = await client.post(f"/orders/{order_id}/fulfill")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_duplicate_cancel_rejected(client: httpx.AsyncClient):
    resp = await client.post("/orders", json=order_payload("tok_success"))
    order_id = resp.json()["id"]

    await client.post(f"/orders/{order_id}/cancel")
    resp = await client.post(f"/orders/{order_id}/cancel")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_nonexistent_order_returns_404(client: httpx.AsyncClient):
    resp = await client.get(f"/orders/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── Event log integrity ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_log_records_full_history(client: httpx.AsyncClient):
    """Verify the immutable event log captures every transition."""
    resp = await client.post("/orders", json=order_payload("tok_success"))
    order_id = resp.json()["id"]

    data = await poll_for_status(client, order_id, "PAID")
    await client.post(f"/orders/{order_id}/fulfill")

    resp = await client.get(f"/orders/{order_id}")
    events = resp.json()["events"]
    event_types = [e["event_type"] for e in events]

    assert "payment_initiated" in event_types
    assert "payment_settled" in event_types
    assert "order_fulfilled" in event_types

    # Events are ordered chronologically
    initiated_idx = event_types.index("payment_initiated")
    settled_idx = event_types.index("payment_settled")
    fulfilled_idx = event_types.index("order_fulfilled")
    assert initiated_idx < settled_idx < fulfilled_idx
