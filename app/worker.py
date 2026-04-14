"""ARQ worker — async Redis queue for notification delivery.

Run with:
    arq app.worker.WorkerSettings

Each task receives a shared ArqRedis pool and a database session factory
via the worker context dict, set up in startup/shutdown hooks.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from arq import ArqRedis
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Notification, NotificationStatus, NotificationType

logger = logging.getLogger(__name__)

# ── Notification templates (mocked delivery) ─────────────────────────────────

TEMPLATES: dict[NotificationType, str] = {
    NotificationType.ORDER_CONFIRMED: "Your order {orderId} has been confirmed. Amount: {amount} {currency}.",
    NotificationType.PAYMENT_FAILED: "Payment failed for order {orderId}. Please update your payment details.",
    NotificationType.ORDER_CANCELLED: "Your order {orderId} has been cancelled.",
    NotificationType.ORDER_FULFILLED: "Great news! Your order {orderId} has been fulfilled and is on its way.",
    NotificationType.ORDER_REFUNDED: "Your refund for order {orderId} has been processed. Amount: {amount} {currency}.",
}


async def _mock_send_email(recipient: str, subject: str, body: str) -> None:
    """Simulate outbound email delivery (replace with SES / SendGrid in prod)."""
    logger.info("EMAIL → %s | %s | %s", recipient, subject, body)


# ── Task ─────────────────────────────────────────────────────────────────────

async def send_notification(ctx: dict, notification_id: str) -> None:
    """Dequeue, render, and (mock-)send a notification, then persist the result."""
    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]

    async with session_factory() as db:
        result = await db.execute(
            select(Notification).where(Notification.id == uuid.UUID(notification_id))
        )
        notif = result.scalar_one_or_none()

        if notif is None:
            logger.error("Notification %s not found — skipping", notification_id)
            return

        if notif.status == NotificationStatus.SENT:
            logger.info("Notification %s already sent — idempotent skip", notification_id)
            return

        template = TEMPLATES.get(notif.type, "Update for order {orderId}.")
        body = template.format(**notif.payload)
        subject = notif.type.value.replace("_", " ").title()

        try:
            await _mock_send_email(notif.recipient, subject, body)
            notif.status = NotificationStatus.SENT
            notif.sent_at = datetime.now(tz=timezone.utc)
            notif.error = None
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to send notification %s", notification_id)
            notif.status = NotificationStatus.FAILED
            notif.error = str(exc)
            raise  # re-raise so ARQ retries the job

        await db.commit()
        logger.info("Notification %s sent successfully", notification_id)


# ── Worker lifecycle ──────────────────────────────────────────────────────────

async def startup(ctx: dict) -> None:
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    ctx["engine"] = engine
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)
    logger.info("Worker startup — DB pool created")


async def shutdown(ctx: dict) -> None:
    await ctx["engine"].dispose()
    logger.info("Worker shutdown — DB pool closed")


# ── Settings consumed by `arq app.worker.WorkerSettings` ─────────────────────

class WorkerSettings:
    functions = [send_notification]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
    )
    max_jobs = 10
    job_timeout = 30
    # Retry a failed notification up to 3 times with exponential backoff
    retry_jobs = True
    max_tries = 3
