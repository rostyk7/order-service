from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest
import pytest_asyncio

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
E2E_TIMEOUT = int(os.getenv("E2E_TIMEOUT", "15"))
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "dev-admin-key")

# Shared with test_full_flow.py — must match exactly (same DB, same 24 h window).
_SA = "e2e-success-a@test.com"
_SB = "e2e-success-b@test.com"

# Emails for fake-card (tok_test) compliance orders — never touch full_flow card tokens.
_CTA = "e2e-comp-a@test.com"
_CTB = "e2e-comp-b@test.com"

# Amounts in cents: 950 000 is inside the structuring band [900 000, 999 999] → score 35 → FLAGGED.
STRUCTURING_AMOUNT = 950_000


async def poll_for_status(
    client: httpx.AsyncClient,
    order_id: str,
    expected: str,
    timeout: int = E2E_TIMEOUT,
) -> dict:
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


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0) as c:
        yield c


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
