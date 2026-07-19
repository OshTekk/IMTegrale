"""Add durable jobs and the notification outbox.

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sync_requests",
        sa.Column("notify", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "sync_requests",
        sa.Column("quota_bypass", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "sync_requests",
        sa.Column("bypass_reason", sa.String(length=240), nullable=True),
    )
    op.add_column(
        "sync_requests",
        sa.Column("force_probe", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "durable_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=96), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('sync', 'calendar')", name="ck_durable_jobs_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'dead_letter')",
            name="ck_durable_jobs_status",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "idempotency_key", name="uq_durable_jobs_kind_idempotency"),
    )
    op.create_index("ix_durable_jobs_account_id", "durable_jobs", ["account_id"])
    op.create_index(
        "ix_durable_jobs_claim",
        "durable_jobs",
        ["kind", "status", "available_at", "priority", "created_at"],
    )
    op.create_index(
        "ix_durable_jobs_expired_lease",
        "durable_jobs",
        ["status", "lease_expires_at"],
    )

    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=96), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('telegram_new_notes')", name="ck_notification_outbox_kind"),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'delivered', 'dead_letter')",
            name="ck_notification_outbox_status",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "kind",
            "idempotency_key",
            name="uq_notification_outbox_kind_idempotency",
        ),
    )
    op.create_index("ix_notification_outbox_account_id", "notification_outbox", ["account_id"])
    op.create_index(
        "ix_notification_outbox_claim",
        "notification_outbox",
        ["status", "available_at", "created_at"],
    )
    op.create_index(
        "ix_notification_outbox_expired_lease",
        "notification_outbox",
        ["status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_outbox_expired_lease", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_claim", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_account_id", table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_index("ix_durable_jobs_expired_lease", table_name="durable_jobs")
    op.drop_index("ix_durable_jobs_claim", table_name="durable_jobs")
    op.drop_index("ix_durable_jobs_account_id", table_name="durable_jobs")
    op.drop_table("durable_jobs")
    op.drop_column("sync_requests", "force_probe")
    op.drop_column("sync_requests", "bypass_reason")
    op.drop_column("sync_requests", "quota_bypass")
    op.drop_column("sync_requests", "notify")
