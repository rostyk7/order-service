from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_redis
from app.dependencies import require_admin
from app.models import OrderStatus
from app.schemas import (
    CreateOrderRequest,
    FulfillOrderRequest,
    OrderListResponse,
    OrderResponse,
    RefundOrderRequest,
    ReviewOrderRequest,
)
from app.services.order_service import (
    cancel_order,
    create_order,
    fulfill_order,
    get_order_or_404,
    list_orders,
    refund_order,
    review_order,
)
from app.services.payment_client import payment_client

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=OrderListResponse)
async def list(
    order_status: Optional[OrderStatus] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    orders, total = await list_orders(db, status=order_status, limit=limit, offset=offset)
    return OrderListResponse(orders=orders, total=total)


@router.post("", response_model=OrderResponse, status_code=status.HTTP_202_ACCEPTED)
async def create(
    body: CreateOrderRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create an order and immediately submit a charge to payment-provider."""
    return await create_order(body, db, payment_client)


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


@router.post("/{order_id}/review", response_model=OrderResponse, dependencies=[Depends(require_admin)])
async def review(
    order_id: str,
    body: ReviewOrderRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """Approve or reject a compliance-flagged order. Requires X-Admin-Key header."""
    order = await get_order_or_404(order_id, db)
    return await review_order(order, body.decision, body.note, db, redis, payment_client)
