"""add REFUNDED order status and ORDER_REFUNDED notification type

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'REFUNDED'")
    op.execute("ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS 'ORDER_REFUNDED'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values — a full type recreation
    # would be needed. For a rollback, redeploy the previous revision instead.
    pass
