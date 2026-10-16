"""Optional leaderboard and isolated administrator portal.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("is_disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("accounts", sa.Column("disabled_at", sa.DateTime(timezone=True)))
    op.add_column("accounts", sa.Column("disabled_reason", sa.String(240)))
    op.add_column(
        "accounts",
        sa.Column("detected_campus", sa.String(16), nullable=False, server_default="unknown"),
    )
    op.add_column("accounts", sa.Column("detected_campus_at", sa.DateTime(timezone=True)))
    op.add_column(
        "accounts",
        sa.Column("campus", sa.String(16), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "accounts",
        sa.Column("campus_source", sa.String(24), nullable=False, server_default="unknown"),
    )
    op.add_column("accounts", sa.Column("campus_confirmed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "accounts",
        sa.Column("cohort", sa.String(16), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "accounts",
        sa.Column("cohort_source", sa.String(24), nullable=False, server_default="unknown"),
    )
    op.add_column("accounts", sa.Column("cohort_confirmed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "accounts",
        sa.Column(
            "classification_review_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "leaderboard_profiles",
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("pseudonym", sa.String(24)),
        sa.Column("pseudonym_key", sa.String(64)),
        sa.Column("is_participating", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("joined_at", sa.DateTime(timezone=True)),
        sa.Column("ranking_visible_at", sa.DateTime(timezone=True)),
        sa.Column("left_at", sa.DateTime(timezone=True)),
        sa.Column("rejoin_after", sa.DateTime(timezone=True)),
        sa.Column("consent_version", sa.String(32)),
        sa.Column("consent_at", sa.DateTime(timezone=True)),
        sa.Column(
            "verification_status", sa.String(24), nullable=False, server_default="standard"
        ),
        sa.Column("suspended_at", sa.DateTime(timezone=True)),
        sa.Column("suspended_reason", sa.String(240)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("pseudonym_key", name="uq_leaderboard_profiles_pseudonym_key"),
    )
    op.create_index(
        "ix_leaderboard_profiles_pseudonym_key",
        "leaderboard_profiles",
        ["pseudonym_key"],
    )
    op.create_index(
        "ix_leaderboard_profiles_is_participating",
        "leaderboard_profiles",
        ["is_participating"],
    )

    op.create_table(
        "admin_users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username", name="uq_admin_users_username"),
    )
    op.create_index("ix_admin_users_username", "admin_users", ["username"], unique=True)

    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "admin_user_id",
            sa.String(36),
            sa.ForeignKey("admin_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("csrf_digest", sa.String(64), nullable=False),
        sa.Column("identity_digest", sa.String(64), nullable=False),
        sa.Column("user_agent", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("digest", name="uq_admin_sessions_digest"),
    )
    op.create_index("ix_admin_sessions_admin_user_id", "admin_sessions", ["admin_user_id"])
    op.create_index("ix_admin_sessions_digest", "admin_sessions", ["digest"], unique=True)
    op.create_index("ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"])

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "admin_user_id",
            sa.String(36),
            sa.ForeignKey("admin_users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "target_account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_audit_logs_admin_user_id", "admin_audit_logs", ["admin_user_id"])
    op.create_index(
        "ix_admin_audit_logs_target_account_id", "admin_audit_logs", ["target_account_id"]
    )
    op.create_index("ix_admin_audit_created_at", "admin_audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("admin_audit_logs")
    op.drop_table("admin_sessions")
    op.drop_table("admin_users")
    op.drop_table("leaderboard_profiles")
    op.drop_column("accounts", "classification_review_required")
    op.drop_column("accounts", "cohort_confirmed_at")
    op.drop_column("accounts", "cohort_source")
    op.drop_column("accounts", "cohort")
    op.drop_column("accounts", "campus_confirmed_at")
    op.drop_column("accounts", "campus_source")
    op.drop_column("accounts", "campus")
    op.drop_column("accounts", "detected_campus_at")
    op.drop_column("accounts", "detected_campus")
    op.drop_column("accounts", "disabled_reason")
    op.drop_column("accounts", "disabled_at")
    op.drop_column("accounts", "is_disabled")
