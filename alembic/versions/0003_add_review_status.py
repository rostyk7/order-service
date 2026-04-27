"""add REVIEW order status

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'REVIEW'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values — redeploy previous revision to roll back.
    pass
