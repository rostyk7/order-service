from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_redis
from app.schemas import CreateOrderRequest, FulfillOrderRequest, OrderResponse, RefundOrderRequest
from app.services.order_service import (
    cancel_order,
    create_order,
    fulfill_order,
    get_order_or_404,
    refund_order,
)
from app.services.payment_client import payment_client

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderResponse, status_code=status.HTTP_202_ACCEPTED)
async def create(
    body: CreateOrderRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """Create an order and immediately submit a charge to payment-provider."""
    return await create_order(body, db, redis, payment_client)


@router.get("/{order_id}", response_model=OrderResponse)
async def get(order_id: str, db: AsyncSession = Depends(get_db)):
    return await get_order_or_404(order_id, db)


@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """Cancel an order. Valid from PENDING, AWAITING_PAYMENT, PAYMENT_FAILED, PAID."""
    order = await get_order_or_404(order_id, db)
    return await cancel_order(order, db, redis)


@router.post("/{order_id}/fulfill", response_model=OrderResponse)
async def fulfill(
    order_id: str,
    body: FulfillOrderRequest = FulfillOrderRequest(),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """Mark a PAID order as fulfilled (goods shipped / service delivered)."""
    order = await get_order_or_404(order_id, db)
    return await fulfill_order(order, body.note, db, redis)


@router.post("/{order_id}/refund", response_model=OrderResponse)
async def refund(
    order_id: str,
    body: RefundOrderRequest = RefundOrderRequest(),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """Refund a PAID order. Calls payment-provider to reverse the charge."""
    order = await get_order_or_404(order_id, db)
    return await refund_order(order, body.reason, db, redis, payment_client)
