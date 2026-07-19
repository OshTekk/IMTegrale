"""Require a passkey second factor for administration.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "admin_sessions",
        sa.Column("password_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "admin_sessions",
        sa.Column("mfa_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "admin_passkey_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("admin_user_id", sa.String(length=36), nullable=False),
        sa.Column("credential_id", sa.String(length=1024), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("transports", sa.JSON(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("device_type", sa.String(length=32), nullable=True),
        sa.Column("backed_up", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_id"),
    )
    op.create_index(
        "ix_admin_passkey_credentials_admin_user_id",
        "admin_passkey_credentials",
        ["admin_user_id"],
    )
    op.create_table(
        "admin_webauthn_challenges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("admin_user_id", sa.String(length=36), nullable=False),
        sa.Column("admin_session_id", sa.String(length=36), nullable=False),
        sa.Column("challenge", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('registration', 'authentication')",
            name="ck_admin_webauthn_challenges_kind",
        ),
        sa.ForeignKeyConstraint(["admin_session_id"], ["admin_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_webauthn_challenges_admin_session_id",
        "admin_webauthn_challenges",
        ["admin_session_id"],
    )
    op.create_index(
        "ix_admin_webauthn_challenges_admin_user_id",
        "admin_webauthn_challenges",
        ["admin_user_id"],
    )
    op.create_index(
        "ix_admin_webauthn_challenges_expires_at",
        "admin_webauthn_challenges",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_webauthn_challenges_expires_at",
        table_name="admin_webauthn_challenges",
    )
    op.drop_index(
        "ix_admin_webauthn_challenges_admin_user_id",
        table_name="admin_webauthn_challenges",
    )
    op.drop_index(
        "ix_admin_webauthn_challenges_admin_session_id",
        table_name="admin_webauthn_challenges",
    )
    op.drop_table("admin_webauthn_challenges")
    op.drop_index(
        "ix_admin_passkey_credentials_admin_user_id",
        table_name="admin_passkey_credentials",
    )
    op.drop_table("admin_passkey_credentials")
    op.drop_column("admin_sessions", "mfa_verified_at")
    op.drop_column("admin_sessions", "password_verified_at")
