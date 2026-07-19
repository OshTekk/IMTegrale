"""Persist correlations and runtime worker freshness.

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ("sync_requests", "durable_jobs", "notification_outbox"):
        op.add_column(
            table_name,
            sa.Column("correlation_id", sa.String(length=36), nullable=True),
        )
        op.create_index(
            f"ix_{table_name}_correlation_id",
            table_name,
            ["correlation_id"],
        )

    op.create_table(
        "runtime_heartbeats",
        sa.Column("component", sa.String(length=24), nullable=False),
        sa.Column("instance_id", sa.String(length=96), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "component IN ('scheduler', 'sync', 'calendar', 'outbox')",
            name="ck_runtime_heartbeats_component",
        ),
        sa.CheckConstraint(
            "state IN ('starting', 'ok', 'error', 'stopping')",
            name="ck_runtime_heartbeats_state",
        ),
        sa.PrimaryKeyConstraint("component"),
    )
    op.create_index(
        "ix_runtime_heartbeats_seen_at",
        "runtime_heartbeats",
        ["seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_heartbeats_seen_at", table_name="runtime_heartbeats")
    op.drop_table("runtime_heartbeats")
    for table_name in reversed(
        ("sync_requests", "durable_jobs", "notification_outbox")
    ):
        op.drop_index(f"ix_{table_name}_correlation_id", table_name=table_name)
        op.drop_column(table_name, "correlation_id")
