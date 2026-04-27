"""Tests for the compliance review endpoint (approve / reject)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

ADMIN_KEY = "dev-admin-key"  # matches config.py default


async def _create_flagged_order(client: AsyncClient) -> str:
    """Helper: create an order that lands in REVIEW via structuring rule."""
    response = await client.post(
        "/orders",
        json={
            "customer_email": "flagged@example.com",
            "amount": 990_000,  # $9,900 — structuring band → FLAGGED
            "currency": "USD",
            "card_token": "tok_review",
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "REVIEW"
    return response.json()["id"]


@pytest.mark.asyncio
async def test_approve_initiates_payment(client: AsyncClient):
    order_id = await _create_flagged_order(client)
    mock_payment = {"id": "pay_approved_01", "status": "PENDING"}

    with patch(
        "app.services.order_service.PaymentClient.create_payment",
        new_callable=AsyncMock,
        return_value=mock_payment,
    ):
        resp = await client.post(
            f"/orders/{order_id}/review",
            json={"decision": "APPROVE", "note": "Verified legitimate"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "AWAITING_PAYMENT"
    assert data["payment_id"] == "pay_approved_01"
    assert any(e["event_type"] == "compliance_cleared" for e in data["events"])


@pytest.mark.asyncio
async def test_reject_cancels_order(client: AsyncClient):
    order_id = await _create_flagged_order(client)

    resp = await client.post(
        f"/orders/{order_id}/review",
        json={"decision": "REJECT", "note": "Confirmed suspicious"},
        headers={"X-Admin-Key": ADMIN_KEY},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "CANCELLED"
    assert any(e["event_type"] == "compliance_rejected" for e in data["events"])


@pytest.mark.asyncio
async def test_reject_enqueues_notification(client: AsyncClient, mock_redis):
    order_id = await _create_flagged_order(client)

    await client.post(
        f"/orders/{order_id}/review",
        json={"decision": "REJECT"},
        headers={"X-Admin-Key": ADMIN_KEY},
    )

    mock_redis.enqueue_job.assert_called_once_with("send_notification", mock_redis.enqueue_job.call_args[0][1])


@pytest.mark.asyncio
async def test_review_requires_admin_key(client: AsyncClient):
    order_id = await _create_flagged_order(client)

    resp = await client.post(
        f"/orders/{order_id}/review",
        json={"decision": "APPROVE"},
        # no X-Admin-Key header
    )
    assert resp.status_code == 422  # missing required header


@pytest.mark.asyncio
async def test_review_wrong_admin_key(client: AsyncClient):
    order_id = await _create_flagged_order(client)

    resp = await client.post(
        f"/orders/{order_id}/review",
        json={"decision": "APPROVE"},
        headers={"X-Admin-Key": "wrong-key"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_review_non_review_order_rejected(client: AsyncClient):
    """Cannot call /review on an order that is not in REVIEW status."""
    mock_payment = {"id": "pay_non_review", "status": "PENDING"}

    with patch(
        "app.services.order_service.PaymentClient.create_payment",
        new_callable=AsyncMock,
        return_value=mock_payment,
    ):
        create = await client.post(
            "/orders",
            json={"customer_email": "b@b.com", "amount": 5000, "card_token": "tok_success"},
        )
    order_id = create.json()["id"]
    assert create.json()["status"] == "AWAITING_PAYMENT"

    resp = await client.post(
        f"/orders/{order_id}/review",
        json={"decision": "APPROVE"},
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_approved_order_completes_full_flow(client: AsyncClient):
    """After approval, the normal webhook flow takes over correctly."""
    order_id = await _create_flagged_order(client)
    payment_id = "pay_full_flow_01"

    with patch(
        "app.services.order_service.PaymentClient.create_payment",
        new_callable=AsyncMock,
        return_value={"id": payment_id, "status": "PENDING"},
    ):
        await client.post(
            f"/orders/{order_id}/review",
            json={"decision": "APPROVE"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )

    # payment-provider fires settled webhook
    webhook = await client.post(
        "/webhooks/payment",
        json={
            "id": "wh-review-001",
            "event": "payment.settled",
            "createdAt": "2026-01-01T00:00:00Z",
            "data": {"transactionId": payment_id, "status": "SETTLED", "referenceId": "BANK-REF-999"},
        },
    )
    assert webhook.status_code == 200

    order = await client.get(f"/orders/{order_id}")
    assert order.json()["status"] == "PAID"
