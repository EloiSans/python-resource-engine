"""api keys

Revision ID: 9b1f04d2c7a8
Revises: 7d2a91c4e0b3
Create Date: 2026-11-26 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b1f04d2c7a8"
down_revision: str | Sequence[str] | None = "7d2a91c4e0b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resource_engine_api_keys",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("tenant", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=300), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_engine_api_keys")),
    )
    with op.batch_alter_table("resource_engine_api_keys", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_resource_engine_api_keys_created_by"), ["created_by"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_resource_engine_api_keys_tenant"), ["tenant"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("resource_engine_api_keys", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_resource_engine_api_keys_tenant"))
        batch_op.drop_index(batch_op.f("ix_resource_engine_api_keys_created_by"))
    op.drop_table("resource_engine_api_keys")
