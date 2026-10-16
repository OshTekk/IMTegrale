"""Initial multi-account schema.

Revision ID: 0001
Revises: None
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("imt_username", sa.String(160), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("encrypted_imt_password", sa.Text(), nullable=False),
        sa.Column("encrypted_telegram_token", sa.Text()),
        sa.Column("encrypted_telegram_chat_id", sa.Text()),
        sa.Column("telegram_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Paris"),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("credentials_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_status", sa.String(32), nullable=False, server_default="never"),
        sa.Column("last_sync_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("imt_username", name="accounts_imt_username_key"),
    )
    op.create_index("ix_accounts_imt_username", "accounts", ["imt_username"], unique=True)

    op.create_table(
        "notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_key", sa.String(96), nullable=False),
        sa.Column("ue_code", sa.String(32), nullable=False),
        sa.Column("raw_label", sa.String(240), nullable=False),
        sa.Column("raw_score", sa.Float(), nullable=False),
        sa.Column("raw_coefficient", sa.Float(), nullable=False),
        sa.Column("raw_is_resit", sa.Boolean(), nullable=False),
        sa.Column("label_override", sa.String(240)),
        sa.Column("score_override", sa.Float()),
        sa.Column("coefficient_override", sa.Float()),
        sa.Column("is_resit_override", sa.Boolean()),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hidden_by_user", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("account_id", "source", "source_key", name="uq_notes_account_source_key"),
    )
    op.create_index("ix_notes_account_id", "notes", ["account_id"])
    op.create_index("ix_notes_account_ue", "notes", ["account_id", "ue_code"])
    op.create_index("ix_notes_account_archived", "notes", ["account_id", "archived"])

    op.create_table(
        "ue_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("credits_ects", sa.Float()),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("year", sa.String(16), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "code", name="uq_ue_settings_account_code"),
    )
    op.create_index("ix_ue_settings_account_id", "ue_settings", ["account_id"])

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_events_account_id", "events", ["account_id"])
    op.create_index("ix_events_account_id_id", "events", ["account_id", "id"])

    op.create_table(
        "share_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("prefix", name="share_tokens_prefix_key"),
    )
    op.create_index("ix_share_tokens_account_id", "share_tokens", ["account_id"])
    op.create_index("ix_share_tokens_prefix", "share_tokens", ["prefix"], unique=True)

    op.create_table(
        "web_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("share_token_id", sa.String(36), sa.ForeignKey("share_tokens.id", ondelete="SET NULL")),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("csrf_digest", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("auth_method", sa.String(16), nullable=False),
        sa.Column("user_agent", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("digest", name="web_sessions_digest_key"),
    )
    op.create_index("ix_web_sessions_account_id", "web_sessions", ["account_id"])
    op.create_index("ix_web_sessions_share_token_id", "web_sessions", ["share_token_id"])
    op.create_index("ix_web_sessions_digest", "web_sessions", ["digest"], unique=True)


def downgrade() -> None:
    op.drop_table("web_sessions")
    op.drop_table("share_tokens")
    op.drop_table("events")
    op.drop_table("ue_settings")
    op.drop_table("notes")
    op.drop_table("accounts")
