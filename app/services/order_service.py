"""Core order business logic — state machine, persistence, queue dispatch."""

from __future__ import annotations

import uuid
import logging
from typing import Any

from arq import ArqRedis
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import (
    Notification,
    NotificationType,
    Order,
    OrderEvent,
    OrderStatus,
    ORDER_TRANSITIONS,
)
from app.schemas import CreateOrderRequest
from app.services.payment_client import PaymentClient, PaymentProviderError

logger = logging.getLogger(__name__)


async def _fetch_order(order_id: uuid.UUID, db: AsyncSession) -> Order:
    """Re-fetch an order with all relationships after a commit.

    populate_existing=True forces SQLAlchemy to overwrite any cached instance
    in the identity map and re-issue the selectinload queries, even when
    expire_on_commit=False is set on the session factory.
    """
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.events),
            selectinload(Order.notifications),
        )
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def get_order_or_404(order_id: str, db: AsyncSession) -> Order:
    result = await db.execute(
        select(Order)
        .where(Order.id == uuid.UUID(order_id))
        .options(
            selectinload(Order.events),
            selectinload(Order.notifications),
        )
    )
    order = result.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


async def _transition(
    order: Order,
    to_status: OrderStatus,
    event_type: str,
    payload: dict,
    db: AsyncSession,
) -> None:
    allowed = ORDER_TRANSITIONS.get(order.status, set())
    if to_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot transition from {order.status} to {to_status}",
        )
    event = OrderEvent(
        order_id=order.id,
        event_type=event_type,
        from_status=order.status,
        to_status=to_status,
        payload=payload,
    )
    order.status = to_status
    db.add(event)


async def create_order(
    body: CreateOrderRequest,
    db: AsyncSession,
    redis: ArqRedis,
    payment_client: PaymentClient,
) -> Order:
    order = Order(
        customer_email=body.customer_email,
        amount=body.amount,
        currency=body.currency,
        card_token=body.card_token,
        metadata_=body.metadata,
    )
    db.add(order)
    await db.flush()  # get the UUID before calling payment-provider

    webhook_url = f"{settings.SELF_BASE_URL}/webhooks/payment"

    try:
        payment = await payment_client.create_payment(
            order_id=str(order.id),
            amount=order.amount,
            currency=order.currency,
            card_token=order.card_token,
            webhook_url=webhook_url,
        )
    except PaymentProviderError as exc:
        logger.error("payment-provider error for order %s: %s", order.id, exc)
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"Payment provider error: {exc.detail}")

    order.payment_id = payment["id"]

    await _transition(
        order,
        OrderStatus.AWAITING_PAYMENT,
        "payment_initiated",
        {"paymentId": payment["id"], "status": payment["status"]},
        db,
    )

    await db.commit()
    return await _fetch_order(order.id, db)


async def handle_payment_webhook(
    order: Order,
    event: str,
    payment_data: dict[str, Any],
    db: AsyncSession,
    redis: ArqRedis,
) -> None:
    """Called by the webhooks router on every inbound payment-provider event."""
    if event == "payment.settled":
        await _transition(
            order,
            OrderStatus.PAID,
            "payment_settled",
            payment_data,
            db,
        )
        notif = _create_notification(order, NotificationType.ORDER_CONFIRMED)
        db.add(notif)
        await db.commit()
        await redis.enqueue_job("send_notification", str(notif.id))

    elif event == "payment.failed":
        await _transition(
            order,
            OrderStatus.PAYMENT_FAILED,
            "payment_failed",
            payment_data,
            db,
        )
        notif = _create_notification(order, NotificationType.PAYMENT_FAILED)
        db.add(notif)
        await db.commit()
        await redis.enqueue_job("send_notification", str(notif.id))

    elif event == "payment.refunded":
        await _transition(
            order,
            OrderStatus.REFUNDED,
            "payment_refunded",
            payment_data,
            db,
        )
        notif = _create_notification(order, NotificationType.ORDER_REFUNDED)
        db.add(notif)
        await db.commit()
        await redis.enqueue_job("send_notification", str(notif.id))

    else:
        # processing / other transitional events — just log, no state change
        logger.info("Ignoring payment event %s for order %s", event, order.id)
        db.add(
            OrderEvent(
                order_id=order.id,
                event_type=event,
                payload=payment_data,
            )
        )
        await db.commit()


async def cancel_order(
    order: Order,
    db: AsyncSession,
    redis: ArqRedis,
) -> Order:
    await _transition(order, OrderStatus.CANCELLED, "order_cancelled", {}, db)
    notif = _create_notification(order, NotificationType.ORDER_CANCELLED)
    db.add(notif)
    await db.commit()
    order = await _fetch_order(order.id, db)
    await redis.enqueue_job("send_notification", str(notif.id))
    return order


async def fulfill_order(
    order: Order,
    note: str,
    db: AsyncSession,
    redis: ArqRedis,
) -> Order:
    await _transition(
        order, OrderStatus.FULFILLED, "order_fulfilled", {"note": note}, db
    )
    notif = _create_notification(order, NotificationType.ORDER_FULFILLED)
    db.add(notif)
    await db.commit()
    order = await _fetch_order(order.id, db)
    await redis.enqueue_job("send_notification", str(notif.id))
    return order


async def refund_order(
    order: Order,
    reason: str,
    db: AsyncSession,
    redis: ArqRedis,
    payment_client: PaymentClient,
) -> Order:
    # Validate state machine BEFORE calling the payment provider — avoid a
    # round-trip to an external service only to fail on a local guard.
    allowed = ORDER_TRANSITIONS.get(order.status, set())
    if OrderStatus.REFUNDED not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot refund an order in status {order.status}",
        )

    if order.payment_id is None:
        raise HTTPException(status_code=409, detail="Order has no associated payment to refund")

    try:
        await payment_client.refund_payment(order.payment_id, reason)
    except PaymentProviderError as exc:
        logger.error("Refund failed for order %s: %s", order.id, exc)
        raise HTTPException(status_code=502, detail=f"Payment provider error: {exc.detail}")

    await _transition(
        order,
        OrderStatus.REFUNDED,
        "order_refunded",
        {"reason": reason, "paymentId": order.payment_id},
        db,
    )
    notif = _create_notification(order, NotificationType.ORDER_REFUNDED)
    db.add(notif)
    await db.commit()
    order = await _fetch_order(order.id, db)
    await redis.enqueue_job("send_notification", str(notif.id))
    return order


def _create_notification(order: Order, type_: NotificationType) -> Notification:
    return Notification(
        order_id=order.id,
        type=type_,
        recipient=order.customer_email,
        payload={"orderId": str(order.id), "amount": order.amount, "currency": order.currency},
    )
