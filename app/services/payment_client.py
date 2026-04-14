"""HTTP client for the payment-provider service."""

from __future__ import annotations

import httpx
from typing import Optional

from app.config import settings


class PaymentProviderError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"payment-provider {status_code}: {detail}")


class PaymentClient:
    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base_url = base_url or settings.PAYMENT_PROVIDER_URL

    async def create_payment(
        self,
        *,
        order_id: str,
        amount: int,
        currency: str,
        card_token: str,
        webhook_url: str,
    ) -> dict:
        """Submit a charge to the payment-provider.

        Uses order_id as the idempotency key — safe to retry on network
        failures without risk of double-charging.
        """
        idempotency_key = f"order-{order_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(
                    f"{self._base_url}/payments",
                    json={
                        "amount": amount,
                        "currency": currency,
                        "merchantId": settings.MERCHANT_ID,
                        "idempotencyKey": idempotency_key,
                        "webhookUrl": webhook_url,
                        "cardToken": card_token,
                        "metadata": {"orderId": order_id},
                    },
                    headers={
                        "Idempotency-Key": idempotency_key,
                        "X-Merchant-Id": settings.MERCHANT_ID,
                    },
                )
            except httpx.RequestError as exc:
                raise PaymentProviderError(503, str(exc)) from exc

        if response.is_error:
            raise PaymentProviderError(response.status_code, response.text)

        return response.json()

    async def refund_payment(self, payment_id: str, reason: str = "") -> dict:
        """Request a refund for a settled payment."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(
                    f"{self._base_url}/payments/{payment_id}/refund",
                    json={"reason": reason},
                )
            except httpx.RequestError as exc:
                raise PaymentProviderError(503, str(exc)) from exc

        if response.is_error:
            raise PaymentProviderError(response.status_code, response.text)

        return response.json()

    async def get_payment(self, payment_id: str) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(f"{self._base_url}/payments/{payment_id}")
            except httpx.RequestError as exc:
                raise PaymentProviderError(503, str(exc)) from exc

        if response.is_error:
            raise PaymentProviderError(response.status_code, response.text)

        return response.json()


# Module-level singleton — re-used across requests via FastAPI DI
payment_client = PaymentClient()
