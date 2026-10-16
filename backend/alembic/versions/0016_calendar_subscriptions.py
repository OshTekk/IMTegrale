"""Add encrypted calendar subscriptions and cached events.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calendar_subscriptions",
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("encrypted_url", sa.Text(), nullable=False),
        sa.Column("url_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("account_hint", sa.String(96), nullable=False),
        sa.Column("content_digest", sa.String(64)),
        sa.Column("etag", sa.String(256)),
        sa.Column("last_modified", sa.String(128)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("next_refresh_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("last_error_code", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "last_status IN ('pending', 'success', 'error')",
            name="ck_calendar_subscriptions_status",
        ),
    )
    op.create_index(
        "ix_calendar_subscriptions_next_refresh",
        "calendar_subscriptions",
        ["next_refresh_at"],
    )

    op.create_table(
        "calendar_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("calendar_subscriptions.account_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("location", sa.String(300)),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("all_day", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("ends_at > starts_at", name="ck_calendar_events_dates"),
        sa.UniqueConstraint(
            "account_id",
            "source_key",
            name="uq_calendar_events_source_key",
        ),
    )
    op.create_index("ix_calendar_events_account_id", "calendar_events", ["account_id"])
    op.create_index(
        "ix_calendar_events_account_start",
        "calendar_events",
        ["account_id", "starts_at"],
    )
    op.create_index(
        "ix_calendar_events_account_end",
        "calendar_events",
        ["account_id", "ends_at"],
    )

    op.create_table(
        "calendar_fetch_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('connect', 'automatic')",
            name="ck_calendar_fetch_attempts_kind",
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'not_modified', 'invalid', 'upstream')",
            name="ck_calendar_fetch_attempts_outcome",
        ),
    )
    op.create_index(
        "ix_calendar_fetch_attempts_account_id",
        "calendar_fetch_attempts",
        ["account_id"],
    )
    op.create_index(
        "ix_calendar_fetch_attempts_account_kind_attempted",
        "calendar_fetch_attempts",
        ["account_id", "kind", "attempted_at"],
    )
    op.create_index(
        "ix_calendar_fetch_attempts_attempted",
        "calendar_fetch_attempts",
        ["attempted_at"],
    )


def downgrade() -> None:
    op.drop_table("calendar_fetch_attempts")
    op.drop_table("calendar_events")
    op.drop_table("calendar_subscriptions")
