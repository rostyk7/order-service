from __future__ import annotations

import enum
import uuid
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    AWAITING_PAYMENT = "AWAITING_PAYMENT"
    PAID = "PAID"
    FULFILLED = "FULFILLED"
    CANCELLED = "CANCELLED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    REFUNDED = "REFUNDED"
    REVIEW = "REVIEW"


# Valid transitions: from_status -> set of allowed to_status
ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.AWAITING_PAYMENT, OrderStatus.CANCELLED, OrderStatus.REVIEW},
    OrderStatus.AWAITING_PAYMENT: {OrderStatus.PAID, OrderStatus.PAYMENT_FAILED, OrderStatus.CANCELLED},
    OrderStatus.PAYMENT_FAILED: {OrderStatus.AWAITING_PAYMENT, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.FULFILLED, OrderStatus.CANCELLED, OrderStatus.REFUNDED},
    OrderStatus.REVIEW: {OrderStatus.AWAITING_PAYMENT, OrderStatus.CANCELLED},
    OrderStatus.FULFILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}


class NotificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class NotificationType(str, enum.Enum):
    ORDER_CONFIRMED = "ORDER_CONFIRMED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_FULFILLED = "ORDER_FULFILLED"
    ORDER_REFUNDED = "ORDER_REFUNDED"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    customer_email: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # in cents
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), nullable=False, default=OrderStatus.PENDING
    )
    payment_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    card_token: Mapped[str] = mapped_column(String, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    events: Mapped[list["OrderEvent"]] = relationship(
        "OrderEvent", back_populates="order", order_by="OrderEvent.created_at"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="order", order_by="Notification.created_at"
    )


class OrderEvent(Base):
    __tablename__ = "order_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    from_status: Mapped[Optional[OrderStatus]] = mapped_column(
        Enum(OrderStatus), nullable=True
    )
    to_status: Mapped[Optional[OrderStatus]] = mapped_column(
        Enum(OrderStatus), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    order: Mapped["Order"] = relationship("Order", back_populates="events")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    type: Mapped[NotificationType] = mapped_column(Enum(NotificationType), nullable=False)
    recipient: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus), nullable=False, default=NotificationStatus.PENDING
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sent_at = mapped_column(DateTime(timezone=True), nullable=True)
    created_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    order: Mapped["Order"] = relationship("Order", back_populates="notifications")
