"""Stop storing public IMT passwords and persist encrypted service sessions.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("auto_sync_paused_reason", sa.String(32)))
    op.add_column("accounts", sa.Column("auto_sync_paused_at", sa.DateTime(timezone=True)))
    op.add_column("accounts", sa.Column("sync_setup_completed_at", sa.DateTime(timezone=True)))
    op.add_column("accounts", sa.Column("last_successful_sync_at", sa.DateTime(timezone=True)))
    op.execute(
        sa.text(
            """
            UPDATE accounts
            SET last_successful_sync_at = last_sync_at
            WHERE last_sync_status = 'success'
            """
        )
    )

    # Password recovery is intentionally impossible after this migration.
    op.drop_column("accounts", "credentials_updated_at")
    op.drop_column("accounts", "encrypted_imt_password")
    op.execute(
        sa.text(
            """
            UPDATE accounts
            SET auto_sync_paused_reason = 'reauth_required',
                auto_sync_paused_at = CURRENT_TIMESTAMP
            WHERE auto_sync_enabled = TRUE
            """
        )
    )

    op.create_table(
        "pass_service_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("encrypted_cookie_jar", sa.Text()),
        sa.Column("state", sa.String(16), nullable=False, server_default="active"),
        sa.Column("established_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("pass_last_success_at", sa.DateTime(timezone=True)),
        sa.Column("hub_last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("hub_last_success_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("end_reason", sa.String(32)),
        sa.Column("reuse_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('active', 'expired', 'revoked', 'invalid')",
            name="ck_pass_service_sessions_state",
        ),
    )
    op.create_index(
        "ix_pass_service_sessions_account_id",
        "pass_service_sessions",
        ["account_id"],
    )
    op.create_index(
        "uq_pass_service_sessions_active_account",
        "pass_service_sessions",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
        sqlite_where=sa.text("state = 'active'"),
    )
    op.create_index(
        "ix_pass_service_sessions_account_state",
        "pass_service_sessions",
        ["account_id", "state"],
    )
    op.create_index(
        "ix_pass_service_sessions_established_at",
        "pass_service_sessions",
        ["established_at"],
    )
    op.create_index(
        "ix_pass_service_sessions_ended_at",
        "pass_service_sessions",
        ["ended_at"],
    )

    op.add_column(
        "leaderboard_profiles",
        sa.Column("refresh_recommended_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("leaderboard_profiles", "refresh_recommended_at")
    op.drop_table("pass_service_sessions")
    op.drop_column("accounts", "sync_setup_completed_at")
    op.drop_column("accounts", "last_successful_sync_at")
    op.drop_column("accounts", "auto_sync_paused_at")
    op.drop_column("accounts", "auto_sync_paused_reason")

    # Deleted passwords cannot be restored. The marker only satisfies the old
    # non-null schema and will force a fresh IMT login before any useful sync.
    op.add_column(
        "accounts",
        sa.Column(
            "encrypted_imt_password",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'removed-by-0017'"),
        ),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "credentials_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE accounts
            SET credentials_updated_at = CURRENT_TIMESTAMP
            """
        )
    )
    with op.batch_alter_table("accounts") as batch:
        batch.alter_column(
            "encrypted_imt_password",
            existing_type=sa.Text(),
            server_default=None,
        )
        batch.alter_column(
            "credentials_updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
