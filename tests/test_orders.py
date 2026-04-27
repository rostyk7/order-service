"""Integration-style tests for the orders router (in-memory DB, mocked deps)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_create_order_success(client: AsyncClient):
    mock_payment = {"id": "pay_123", "status": "PENDING"}

    with patch(
        "app.services.order_service.PaymentClient.create_payment",
        new_callable=AsyncMock,
        return_value=mock_payment,
    ):
        response = await client.post(
            "/orders",
            json={
                "customer_email": "buyer@example.com",
                "amount": 9900,  # well under $10k — passes compliance
                "currency": "USD",
                "card_token": "tok_success",
            },
        )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "AWAITING_PAYMENT"
    assert data["payment_id"] == "pay_123"
    assert data["amount"] == 9900
    assert data["customer_email"] == "buyer@example.com"
    assert any(e["event_type"] == "payment_initiated" for e in data["events"])


@pytest.mark.asyncio
async def test_create_order_compliance_blocked(client: AsyncClient):
    # card_sharing fires when card_distinct_emails_last_24h > 2 (score=50 → BLOCKED).
    # Create 3 orders with different emails on the same card to set up history.
    card = "tok_card_shared"
    mock_payment = {"id": "pay_setup", "status": "PENDING"}
    for email in ["a@shared.com", "b@shared.com", "c@shared.com"]:
        with patch(
            "app.services.order_service.PaymentClient.create_payment",
            new_callable=AsyncMock,
            return_value=mock_payment,
        ):
            await client.post(
                "/orders",
                json={"customer_email": email, "amount": 5000, "currency": "USD", "card_token": card},
            )

    # 4th order with the same card — 3 distinct prior emails triggers card_sharing → BLOCKED
    response = await client.post(
        "/orders",
        json={
            "customer_email": "d@shared.com",
            "amount": 5000,
            "currency": "USD",
            "card_token": card,
        },
    )
    assert response.status_code == 422
    assert "blocked" in response.json()["detail"]["message"].lower()


@pytest.mark.asyncio
async def test_create_order_compliance_flagged(client: AsyncClient):
    response = await client.post(
        "/orders",
        json={
            "customer_email": "buyer@example.com",
            "amount": 990_000,  # $9,900 — triggers structuring → FLAGGED
            "currency": "USD",
            "card_token": "tok_success",
        },
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "REVIEW"
    assert any(e["event_type"] == "compliance_flagged" for e in data["events"])


@pytest.mark.asyncio
async def test_create_order_sanctioned_currency_blocked(client: AsyncClient):
    response = await client.post(
        "/orders",
        json={
            "customer_email": "buyer@example.com",
            "amount": 5000,
            "currency": "IRR",  # sanctioned → BLOCKED
            "card_token": "tok_success",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_order_validates_amount(client: AsyncClient):
    response = await client.post(
        "/orders",
        json={
            "customer_email": "buyer@example.com",
            "amount": 0,
            "card_token": "tok_success",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_order_payment_provider_error(client: AsyncClient):
    from app.services.payment_client import PaymentProviderError

    with patch(
        "app.services.order_service.PaymentClient.create_payment",
        new_callable=AsyncMock,
        side_effect=PaymentProviderError(500, "internal error"),
    ):
        response = await client.post(
            "/orders",
            json={
                "customer_email": "buyer@example.com",
                "amount": 9900,
                "card_token": "tok_success",
            },
        )

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_get_order_not_found(client: AsyncClient):
    import uuid

    response = await client.get(f"/orders/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancel_order(client: AsyncClient):
    mock_payment = {"id": "pay_456", "status": "PENDING"}

    with patch(
        "app.services.order_service.PaymentClient.create_payment",
        new_callable=AsyncMock,
        return_value=mock_payment,
    ):
        create_resp = await client.post(
            "/orders",
            json={
                "customer_email": "buyer@example.com",
                "amount": 5000,
                "card_token": "tok_success",
            },
        )
    assert create_resp.status_code == 202
    order_id = create_resp.json()["id"]

    cancel_resp = await client.post(f"/orders/{order_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_double_cancel_is_rejected(client: AsyncClient):
    mock_payment = {"id": "pay_789", "status": "PENDING"}

    with patch(
        "app.services.order_service.PaymentClient.create_payment",
        new_callable=AsyncMock,
        return_value=mock_payment,
    ):
        create_resp = await client.post(
            "/orders",
            json={
                "customer_email": "buyer@example.com",
                "amount": 5000,
                "card_token": "tok_success",
            },
        )
    order_id = create_resp.json()["id"]

    await client.post(f"/orders/{order_id}/cancel")
    second = await client.post(f"/orders/{order_id}/cancel")
    assert second.status_code == 409
