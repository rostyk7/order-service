"""Unit tests for the webhook handler — state transitions driven by payment events."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def _create_order(client: AsyncClient, payment_id: str = "pay_wh_001") -> str:
    with patch(
        "app.services.order_service.PaymentClient.create_payment",
        new_callable=AsyncMock,
        return_value={"id": payment_id, "status": "PENDING"},
    ):
        resp = await client.post(
            "/orders",
            json={
                "customer_email": "wh@example.com",
                "amount": 2000,
                "card_token": "tok_success",
            },
        )
    assert resp.status_code == 202
    return resp.json()["id"]


def _webhook_payload(payment_id: str, event: str, status: str = "SETTLED") -> dict:
    return {
        "id": "wh-delivery-001",
        "event": event,
        "createdAt": "2024-01-01T00:00:00Z",
        "data": {
            "transactionId": payment_id,
            "status": status,
            "referenceId": "BANK-REF-123",
        },
    }


@pytest.mark.asyncio
async def test_payment_settled_transitions_to_paid(client: AsyncClient):
    payment_id = "pay_settled_01"
    order_id = await _create_order(client, payment_id)

    resp = await client.post(
        "/webhooks/payment",
        json=_webhook_payload(payment_id, "payment.settled", "SETTLED"),
    )
    assert resp.status_code == 200

    order = await client.get(f"/orders/{order_id}")
    assert order.json()["status"] == "PAID"
    events = order.json()["events"]
    assert any(e["event_type"] == "payment_settled" for e in events)


@pytest.mark.asyncio
async def test_payment_failed_transitions_to_payment_failed(client: AsyncClient):
    payment_id = "pay_failed_01"
    order_id = await _create_order(client, payment_id)

    resp = await client.post(
        "/webhooks/payment",
        json=_webhook_payload(payment_id, "payment.failed", "FAILED"),
    )
    assert resp.status_code == 200

    order = await client.get(f"/orders/{order_id}")
    assert order.json()["status"] == "PAYMENT_FAILED"


@pytest.mark.asyncio
async def test_webhook_for_unknown_payment_returns_200(client: AsyncClient):
    """payment-provider retries on non-2xx; we must return 200 for unknown IDs."""
    resp = await client.post(
        "/webhooks/payment",
        json=_webhook_payload("pay_unknown_xyz", "payment.settled"),
    )
    assert resp.status_code == 200
    assert resp.json()["received"] is True


@pytest.mark.asyncio
async def test_settled_webhook_enqueues_notification(client: AsyncClient, mock_redis):
    payment_id = "pay_notif_01"
    await _create_order(client, payment_id)

    await client.post(
        "/webhooks/payment",
        json=_webhook_payload(payment_id, "payment.settled"),
    )

    mock_redis.enqueue_job.assert_called_once()
    call_args = mock_redis.enqueue_job.call_args
    assert call_args[0][0] == "send_notification"


@pytest.mark.asyncio
async def test_duplicate_settled_webhook_is_idempotent(client: AsyncClient):
    """Second delivery of the same event should be rejected by the state machine (409)
    but the webhook endpoint itself always returns 200 — it absorbs the conflict."""
    payment_id = "pay_dup_01"
    order_id = await _create_order(client, payment_id)

    payload = _webhook_payload(payment_id, "payment.settled")
    first = await client.post("/webhooks/payment", json=payload)
    assert first.status_code == 200

    # second delivery — the order is already PAID, transition would be invalid
    # the webhook handler catches the 409 and still returns 200 to stop retries
    second = await client.post("/webhooks/payment", json=payload)
    assert second.status_code == 200
