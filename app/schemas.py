from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.models import NotificationStatus, NotificationType, OrderStatus


# ── Requests ─────────────────────────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    customer_email: str
    amount: int  # in cents, must be positive
    currency: str = "USD"
    card_token: str
    metadata: dict[str, Any] = {}

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("amount must be a positive integer (cents)")
        return v

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, v: str) -> str:
        return v.upper()


class FulfillOrderRequest(BaseModel):
    note: str = ""


class RefundOrderRequest(BaseModel):
    reason: str = "Customer request"


# ── Responses ────────────────────────────────────────────────────────────────

class OrderEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    from_status: Optional[OrderStatus]
    to_status: Optional[OrderStatus]
    payload: dict
    created_at: datetime


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: NotificationType
    status: NotificationStatus
    recipient: str
    created_at: datetime


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_email: str
    amount: int
    currency: str
    status: OrderStatus
    payment_id: Optional[str]
    metadata_: dict
    created_at: datetime
    updated_at: datetime
    events: list[OrderEventResponse] = []
    notifications: list[NotificationResponse] = []


# ── Webhook payload from payment-provider ────────────────────────────────────

class PaymentWebhookData(BaseModel):
    transactionId: str
    status: str
    referenceId: Optional[str] = None
    errorCode: Optional[str] = None


class PaymentWebhookPayload(BaseModel):
    id: str
    event: str
    createdAt: str
    data: PaymentWebhookData
