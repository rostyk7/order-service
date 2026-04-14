"""Receive inbound webhook events from payment-provider."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_redis
from app.models import Order
from app.schemas import PaymentWebhookPayload
from app.services.order_service import handle_payment_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/payment", status_code=status.HTTP_200_OK)
async def payment_webhook(
    payload: PaymentWebhookPayload,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """
    Receives payment.processing / payment.settled / payment.failed /
    payment.refunded events from payment-provider.

    Idempotency: payment-provider may retry delivery — processing the same
    event twice is safe because the state machine blocks invalid transitions.
    """
    logger.info(
        "Received payment webhook event=%s transaction=%s",
        payload.event,
        payload.data.transactionId,
    )

    # Look up the order by the payment_id stored at charge time
    result = await db.execute(
        select(Order)
        .where(Order.payment_id == payload.data.transactionId)
        .options(
            selectinload(Order.events),
            selectinload(Order.notifications),
        )
    )
    order = result.scalar_one_or_none()

    if order is None:
        # payment-provider may fire events for payments we don't know about
        # (e.g. created directly against the provider). Return 200 so it
        # doesn't keep retrying.
        logger.warning(
            "No order found for payment %s — ignoring event %s",
            payload.data.transactionId,
            payload.event,
        )
        return {"received": True}

    try:
        await handle_payment_webhook(
            order,
            payload.event,
            payload.data.model_dump(),
            db,
            redis,
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            # Duplicate delivery — state machine already handled this event.
            # Return 200 so payment-provider stops retrying.
            logger.info(
                "Duplicate webhook event=%s for order %s — absorbing conflict",
                payload.event,
                order.id,
            )
        else:
            raise

    return {"received": True}