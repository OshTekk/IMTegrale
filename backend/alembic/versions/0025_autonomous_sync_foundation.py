"""Add explicit sync modes and an empty credential foundation.

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

SYNC_MODE_VALUES = ("manual", "session_only", "autonomous")
SYNC_MODE_CHECK = (
    "auto_sync_mode IN ("
    + ", ".join(f"'{mode}'" for mode in SYNC_MODE_VALUES)
    + ")"
)


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "auto_sync_mode",
            sa.String(length=24),
            nullable=True,
            server_default=sa.text("'manual'"),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE accounts SET auto_sync_mode = "
            "CASE WHEN auto_sync_enabled THEN 'session_only' ELSE 'manual' END"
        )
    )
    with op.batch_alter_table("accounts") as batch:
        batch.alter_column(
            "auto_sync_mode",
            existing_type=sa.String(length=24),
            nullable=False,
            server_default=sa.text("'manual'"),
        )
        batch.create_check_constraint(
            "ck_accounts_auto_sync_mode",
            SYNC_MODE_CHECK,
        )

    op.create_table(
        "imt_sync_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("encrypted_envelope", sa.LargeBinary(), nullable=True),
        sa.Column("envelope_version", sa.Integer(), nullable=True),
        sa.Column("key_id", sa.String(length=64), nullable=True),
        sa.Column(
            "credential_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "failure_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "state IN ('active', 'invalid', 'revoked')",
            name="ck_imt_sync_credentials_state",
        ),
        sa.CheckConstraint(
            "credential_generation >= 1",
            name="ck_imt_sync_credentials_generation",
        ),
        sa.CheckConstraint(
            "consent_version >= 1",
            name="ck_imt_sync_credentials_consent_version",
        ),
        sa.CheckConstraint(
            "failure_count >= 0",
            name="ck_imt_sync_credentials_failure_count",
        ),
        sa.CheckConstraint(
            "encrypted_envelope IS NULL "
            "OR length(encrypted_envelope) BETWEEN 48 AND 4096",
            name="ck_imt_sync_credentials_envelope_size",
        ),
        sa.CheckConstraint(
            "envelope_version IS NULL OR envelope_version >= 1",
            name="ck_imt_sync_credentials_envelope_version",
        ),
        sa.CheckConstraint(
            "key_id IS NULL OR length(key_id) BETWEEN 1 AND 64",
            name="ck_imt_sync_credentials_key_id_size",
        ),
        sa.CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN ("
            "'manual_mode', 'session_only_mode', 'pass_access_purged', "
            "'credential_replaced', 'credential_invalid', 'key_unavailable'"
            ")",
            name="ck_imt_sync_credentials_revoked_reason",
        ),
        sa.CheckConstraint(
            "state != 'active' OR ("
            "encrypted_envelope IS NOT NULL "
            "AND envelope_version >= 1 "
            "AND key_id IS NOT NULL "
            "AND verified_at IS NOT NULL"
            ")",
            name="ck_imt_sync_credentials_active_fields",
        ),
        sa.CheckConstraint(
            "state = 'active' OR encrypted_envelope IS NULL",
            name="ck_imt_sync_credentials_inactive_envelope",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_imt_sync_credentials_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_imt_sync_credentials"),
        sa.UniqueConstraint(
            "account_id",
            name="uq_imt_sync_credentials_account",
        ),
    )


def downgrade() -> None:
    op.drop_table("imt_sync_credentials")
    with op.batch_alter_table("accounts") as batch:
        batch.drop_constraint("ck_accounts_auto_sync_mode", type_="check")
        batch.drop_column("auto_sync_mode")
