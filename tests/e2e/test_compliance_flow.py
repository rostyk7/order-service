from __future__ import annotations

import uuid

import httpx
import pytest

from tests.e2e.constants import ADMIN_KEY, STRUCTURING_AMOUNT, _CTA, _CTB, _SB
from tests.e2e.helpers import poll_for_status


# ── BLOCKED ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compliance_blocked_currency(client: httpx.AsyncClient):
    """Sanctioned currency → 422, order rolled back and never persisted."""
    resp = await client.post("/orders", json={
        "customer_email": f"e2e-irr-{uuid.uuid4().hex[:6]}@test.com",
        "amount": 5000,
        "currency": "IRR",
        "card_token": "tok_success",
        "metadata": {},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "currency_blacklist" in detail["rules_fired"]
    assert detail["risk_score"] == 100


# ── REVIEW → APPROVE ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compliance_review_approve_full_flow(client: httpx.AsyncClient):
    """Structuring amount → REVIEW; admin approves → payment settles → PAID."""
    resp = await client.post("/orders", json={
        "customer_email": _SB,
        "amount": STRUCTURING_AMOUNT,
        "currency": "USD",
        "card_token": "tok_success",
        "metadata": {},
    })
    assert resp.status_code == 202
    data = resp.json()
    order_id = data["id"]
    assert data["status"] == "REVIEW"
    assert data["payment_id"] is None
    flagged_event = next(e for e in data["events"] if e["event_type"] == "compliance_flagged")
    assert "structuring" in flagged_event["payload"]["rules_fired"]

    resp = await client.post(
        f"/orders/{order_id}/review",
        json={"decision": "APPROVE", "note": "Manually verified — legitimate purchase"},
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "AWAITING_PAYMENT"
    assert data["payment_id"] is not None

    data = await poll_for_status(client, order_id, "PAID")
    assert data["status"] == "PAID"
    assert any(e["event_type"] == "compliance_cleared" for e in data["events"])
    assert any(e["event_type"] == "payment_settled" for e in data["events"])


# ── REVIEW → REJECT ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compliance_review_reject(client: httpx.AsyncClient):
    """Structuring amount → REVIEW; admin rejects → CANCELLED. Second review → 409."""
    resp = await client.post("/orders", json={
        "customer_email": _CTA,
        "amount": STRUCTURING_AMOUNT,
        "currency": "USD",
        "card_token": "tok_test",
        "metadata": {},
    })
    assert resp.status_code == 202
    order_id = resp.json()["id"]
    assert resp.json()["status"] == "REVIEW"

    resp = await client.post(
        f"/orders/{order_id}/review",
        json={"decision": "REJECT", "note": "Suspicious structuring pattern"},
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "CANCELLED"
    assert any(e["event_type"] == "compliance_rejected" for e in data["events"])
    assert any(n["type"] == "ORDER_CANCELLED" for n in data["notifications"])

    # Already cancelled — cannot review again
    resp = await client.post(
        f"/orders/{order_id}/review",
        json={"decision": "APPROVE", "note": ""},
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert resp.status_code == 409


# ── Admin auth guards ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compliance_admin_missing_key(client: httpx.AsyncClient):
    """Review endpoint without X-Admin-Key header returns 422."""
    resp = await client.post("/orders", json={
        "customer_email": _CTB,
        "amount": STRUCTURING_AMOUNT,
        "currency": "USD",
        "card_token": "tok_test",
        "metadata": {},
    })
    assert resp.status_code == 202
    order_id = resp.json()["id"]

    resp = await client.post(
        f"/orders/{order_id}/review",
        json={"decision": "APPROVE", "note": ""},
        # no X-Admin-Key header
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_compliance_admin_wrong_key(client: httpx.AsyncClient):
    """Review endpoint with incorrect X-Admin-Key returns 403."""
    resp = await client.post("/orders", json={
        "customer_email": _CTA,
        "amount": STRUCTURING_AMOUNT,
        "currency": "USD",
        "card_token": "tok_test",
        "metadata": {},
    })
    assert resp.status_code == 202
    order_id = resp.json()["id"]

    resp = await client.post(
        f"/orders/{order_id}/review",
        json={"decision": "APPROVE", "note": ""},
        headers={"X-Admin-Key": "not-the-right-key"},
    )
    assert resp.status_code == 403
