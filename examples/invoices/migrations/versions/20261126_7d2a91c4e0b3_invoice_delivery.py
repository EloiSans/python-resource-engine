"""invoice delivery

Revision ID: 7d2a91c4e0b3
Revises: 35493a6f037a
Create Date: 2026-11-26 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d2a91c4e0b3"
down_revision: str | Sequence[str] | None = "35493a6f037a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "delivery_status", sa.String(length=32), server_default="unsent", nullable=False
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.drop_column("delivery_status")
