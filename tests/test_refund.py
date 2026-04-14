"""Tests for the refund flow — endpoint, webhook, and state machine guard."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def _create_paid_order(client: AsyncClient, payment_id: str) -> str:
    """Helper: create an order and drive it to PAID via a settled webhook."""
    with patch(
        "app.services.order_service.PaymentClient.create_payment",
        new_callable=AsyncMock,
        return_value={"id": payment_id, "status": "PENDING"},
    ):
        resp = await client.post(
            "/orders",
            json={
                "customer_email": "buyer@example.com",
                "amount": 7500,
                "card_token": "tok_success",
            },
        )
    assert resp.status_code == 202
    order_id = resp.json()["id"]

    webhook = await client.post(
        "/webhooks/payment",
        json={
            "id": "wh-001",
            "event": "payment.settled",
            "createdAt": "2024-01-01T00:00:00Z",
            "data": {
                "transactionId": payment_id,
                "status": "SETTLED",
                "referenceId": "BANK-REF-001",
            },
        },
    )
    assert webhook.status_code == 200
    return order_id


@pytest.mark.asyncio
async def test_refund_paid_order(client: AsyncClient, mock_redis):
    payment_id = "pay_refund_01"
    order_id = await _create_paid_order(client, payment_id)

    with patch(
        "app.services.order_service.PaymentClient.refund_payment",
        new_callable=AsyncMock,
        return_value={"id": payment_id, "status": "REFUNDED"},
    ):
        resp = await client.post(
            f"/orders/{order_id}/refund",
            json={"reason": "Customer request"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "REFUNDED"
    assert any(e["event_type"] == "order_refunded" for e in data["events"])


@pytest.mark.asyncio
async def test_refund_enqueues_notification(client: AsyncClient, mock_redis):
    payment_id = "pay_refund_02"
    order_id = await _create_paid_order(client, payment_id)

    with patch(
        "app.services.order_service.PaymentClient.refund_payment",
        new_callable=AsyncMock,
        return_value={"id": payment_id, "status": "REFUNDED"},
    ):
        await client.post(f"/orders/{order_id}/refund")

    # settled webhook + refund = 2 enqueue calls
    assert mock_redis.enqueue_job.call_count == 2
    last_call = mock_redis.enqueue_job.call_args_list[-1]
    assert last_call[0][0] == "send_notification"


@pytest.mark.asyncio
async def test_refund_pending_order_rejected(client: AsyncClient):
    """Cannot refund an order that was never paid."""
    with patch(
        "app.services.order_service.PaymentClient.create_payment",
        new_callable=AsyncMock,
        return_value={"id": "pay_refund_03", "status": "PENDING"},
    ):
        create = await client.post(
            "/orders",
            json={"customer_email": "b@b.com", "amount": 1000, "card_token": "tok_success"},
        )
    order_id = create.json()["id"]

    resp = await client.post(f"/orders/{order_id}/refund")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_refund_fulfilled_order_rejected(client: AsyncClient):
    """Cannot refund once the order is already fulfilled."""
    payment_id = "pay_refund_04"
    order_id = await _create_paid_order(client, payment_id)

    await client.post(f"/orders/{order_id}/fulfill")

    with patch(
        "app.services.order_service.PaymentClient.refund_payment",
        new_callable=AsyncMock,
    ):
        resp = await client.post(f"/orders/{order_id}/refund")

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_refund_payment_provider_error(client: AsyncClient):
    """502 returned when payment-provider rejects the refund."""
    from app.services.payment_client import PaymentProviderError

    payment_id = "pay_refund_05"
    order_id = await _create_paid_order(client, payment_id)

    with patch(
        "app.services.order_service.PaymentClient.refund_payment",
        new_callable=AsyncMock,
        side_effect=PaymentProviderError(422, "already refunded"),
    ):
        resp = await client.post(f"/orders/{order_id}/refund")

    assert resp.status_code == 502
    # order must stay PAID — no partial state change
    order = await client.get(f"/orders/{order_id}")
    assert order.json()["status"] == "PAID"


@pytest.mark.asyncio
async def test_payment_refunded_webhook_transitions_order(client: AsyncClient):
    """payment.refunded webhook drives the order to REFUNDED independently."""
    payment_id = "pay_refund_wh_01"
    order_id = await _create_paid_order(client, payment_id)

    resp = await client.post(
        "/webhooks/payment",
        json={
            "id": "wh-refund-001",
            "event": "payment.refunded",
            "createdAt": "2024-01-01T00:00:00Z",
            "data": {
                "transactionId": payment_id,
                "status": "REFUNDED",
                "referenceId": "BANK-REF-REFUND",
            },
        },
    )
    assert resp.status_code == 200

    order = await client.get(f"/orders/{order_id}")
    assert order.json()["status"] == "REFUNDED"
    events = order.json()["events"]
    assert any(e["event_type"] == "payment_refunded" for e in events)
