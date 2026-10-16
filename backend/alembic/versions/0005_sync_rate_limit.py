"""Atomic synchronization cooldown and idempotency.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("sync_cooldown_until", sa.DateTime(timezone=True)))
    op.add_column("accounts", sa.Column("sync_active_request_id", sa.String(length=36)))
    op.add_column("accounts", sa.Column("sync_active_until", sa.DateTime(timezone=True)))
    op.create_table(
        "sync_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_digest", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("result", sa.JSON()),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'skipped')",
            name="ck_sync_requests_status",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "idempotency_digest",
            name="uq_sync_requests_account_idempotency",
        ),
    )
    op.create_index(
        "ix_sync_requests_account_id",
        "sync_requests",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        "ix_sync_requests_account_status",
        "sync_requests",
        ["account_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sync_requests_account_status", table_name="sync_requests")
    op.drop_index("ix_sync_requests_account_id", table_name="sync_requests")
    op.drop_table("sync_requests")
    op.drop_column("accounts", "sync_active_until")
    op.drop_column("accounts", "sync_active_request_id")
    op.drop_column("accounts", "sync_cooldown_until")
