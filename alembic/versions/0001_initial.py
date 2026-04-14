"""initial schema

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE orderstatus AS ENUM
                ('PENDING','AWAITING_PAYMENT','PAID','FULFILLED','CANCELLED','PAYMENT_FAILED');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE notificationstatus AS ENUM ('PENDING','SENT','FAILED');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE notificationtype AS ENUM
                ('ORDER_CONFIRMED','PAYMENT_FAILED','ORDER_CANCELLED','ORDER_FULFILLED');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_email", sa.String(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "AWAITING_PAYMENT",
                "PAID",
                "FULFILLED",
                "CANCELLED",
                "PAYMENT_FAILED",
                name="orderstatus",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("payment_id", sa.String(), nullable=True),
        sa.Column("card_token", sa.String(), nullable=False),
        sa.Column("metadata", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "order_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column(
            "from_status",
            sa.Enum("PENDING", "AWAITING_PAYMENT", "PAID", "FULFILLED", "CANCELLED", "PAYMENT_FAILED",
                    name="orderstatus", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            sa.Enum("PENDING", "AWAITING_PAYMENT", "PAID", "FULFILLED", "CANCELLED", "PAYMENT_FAILED",
                    name="orderstatus", create_type=False),
            nullable=True,
        ),
        sa.Column("payload", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_order_events_order_id", "order_events", ["order_id"])

    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column(
            "type",
            sa.Enum("ORDER_CONFIRMED", "PAYMENT_FAILED", "ORDER_CANCELLED", "ORDER_FULFILLED",
                    name="notificationtype", create_type=False),
            nullable=False,
        ),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "SENT", "FAILED", name="notificationstatus", create_type=False),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("payload", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_notifications_order_id", "notifications", ["order_id"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("order_events")
    op.drop_table("orders")
    op.execute("DROP TYPE IF EXISTS notificationtype")
    op.execute("DROP TYPE IF EXISTS notificationstatus")
    op.execute("DROP TYPE IF EXISTS orderstatus")
