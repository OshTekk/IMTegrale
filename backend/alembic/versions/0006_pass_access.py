"""PASS access gateway, passkeys, adaptive sync and official promotions.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    account_columns = (
        sa.Column("auto_sync_adaptive", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_sync_current_interval_hours", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("auto_sync_no_change_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("auto_sync_next_at", sa.DateTime(timezone=True)),
        sa.Column("profile_refreshed_at", sa.DateTime(timezone=True)),
        sa.Column("profile_refresh_requested_at", sa.DateTime(timezone=True)),
        sa.Column("detected_program", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("detected_promotion_year", sa.Integer()),
        sa.Column("program", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("promotion_year", sa.Integer()),
        sa.Column("academic_source", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("academic_verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_note_change_at", sa.DateTime(timezone=True)),
        sa.Column("security_setup_completed_at", sa.DateTime(timezone=True)),
    )
    for column in account_columns:
        op.add_column("accounts", column)
    op.execute(
        sa.text(
            "UPDATE accounts SET auto_sync_current_interval_hours = auto_sync_interval_hours, "
            "profile_refreshed_at = detected_campus_at, "
            "security_setup_completed_at = CURRENT_TIMESTAMP"
        )
    )
    if op.get_bind().dialect.name != "sqlite":
        op.create_check_constraint(
            "ck_accounts_auto_sync_current_interval",
            "accounts",
            "auto_sync_current_interval_hours IN (2, 4, 6, 8, 12, 24)",
        )

    op.create_table(
        "pass_system_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("active_operation_id", sa.String(36)),
        sa.Column("active_until", sa.DateTime(timezone=True)),
        sa.Column("quiet_until", sa.DateTime(timezone=True)),
        sa.Column("circuit_state", sa.String(16), nullable=False, server_default="closed"),
        sa.Column("circuit_open_until", sa.DateTime(timezone=True)),
        sa.Column("circuit_reason", sa.String(64)),
        sa.Column("circuit_failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("probe_operation_id", sa.String(36)),
        sa.Column("auth_blocked_until", sa.DateTime(timezone=True)),
        sa.Column("auth_block_reason", sa.String(64)),
        sa.Column("last_auto_account_id", sa.String(36)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        sa.text(
            "INSERT INTO pass_system_state "
            "(id, circuit_state, circuit_failure_count, updated_at) "
            "VALUES (1, 'closed', 0, CURRENT_TIMESTAMP)"
        )
    )

    op.create_table(
        "pass_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="SET NULL")),
        sa.Column("target_ref", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("quota_bypassed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("bypass_reason", sa.String(240)),
        sa.Column("is_probe", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("session_reused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("full_sso_performed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("profile_fetched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_class", sa.String(64)),
        sa.Column("upstream_status", sa.Integer()),
        sa.Column("retry_after_seconds", sa.Integer()),
    )
    op.create_index("ix_pass_operations_account_id", "pass_operations", ["account_id"])
    op.create_index("ix_pass_operations_target_ref", "pass_operations", ["target_ref"])
    op.create_index("ix_pass_operations_started_at", "pass_operations", ["started_at"])
    op.create_index("ix_pass_operations_target_started", "pass_operations", ["target_ref", "started_at"])
    op.create_index("ix_pass_operations_kind_started", "pass_operations", ["kind", "started_at"])
    op.create_index("ix_pass_operations_status_started", "pass_operations", ["status", "started_at"])

    op.create_table(
        "pass_denials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="SET NULL")),
        sa.Column("target_ref", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pass_denials_account_id", "pass_denials", ["account_id"])
    op.create_index("ix_pass_denials_reason_created", "pass_denials", ["reason", "created_at"])

    op.create_table(
        "auth_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("target_ref", sa.String(64), nullable=False),
        sa.Column("client_ref", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_attempts_client_attempted", "auth_attempts", ["client_ref", "attempted_at"])
    op.create_index("ix_auth_attempts_target_attempted", "auth_attempts", ["target_ref", "attempted_at"])
    op.create_index("ix_auth_attempts_outcome_attempted", "auth_attempts", ["outcome", "attempted_at"])

    op.create_table(
        "auth_throttle_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("reference", sa.String(64), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("escalation_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_until", sa.DateTime(timezone=True)),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_escalated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("scope", "reference", name="uq_auth_throttle_scope_reference"),
    )

    op.create_table(
        "passkey_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("credential_id", sa.String(1024), nullable=False, unique=True),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transports", sa.JSON(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("device_type", sa.String(32)),
        sa.Column("backed_up", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_passkey_credentials_account_id", "passkey_credentials", ["account_id"])
    op.create_table(
        "webauthn_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE")),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("web_sessions.id", ondelete="CASCADE")),
        sa.Column("challenge", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_webauthn_challenges_account_id", "webauthn_challenges", ["account_id"])
    op.create_index("ix_webauthn_challenges_session_id", "webauthn_challenges", ["session_id"])
    op.create_index("ix_webauthn_challenges_expires_at", "webauthn_challenges", ["expires_at"])

    op.create_table(
        "cohort_pulses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("program", sa.String(32), nullable=False),
        sa.Column("promotion_year", sa.Integer(), nullable=False),
        sa.Column("last_emitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("affected_accounts", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("program", "promotion_year", name="uq_cohort_pulse_segment"),
    )


def downgrade() -> None:
    op.drop_table("cohort_pulses")
    op.drop_table("webauthn_challenges")
    op.drop_table("passkey_credentials")
    op.drop_table("auth_throttle_states")
    op.drop_table("auth_attempts")
    op.drop_table("pass_denials")
    op.drop_table("pass_operations")
    op.drop_table("pass_system_state")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("ck_accounts_auto_sync_current_interval", "accounts", type_="check")
    for name in (
        "security_setup_completed_at",
        "last_note_change_at",
        "academic_verified_at",
        "academic_source",
        "promotion_year",
        "program",
        "detected_promotion_year",
        "detected_program",
        "profile_refresh_requested_at",
        "profile_refreshed_at",
        "auto_sync_next_at",
        "auto_sync_no_change_streak",
        "auto_sync_current_interval_hours",
        "auto_sync_adaptive",
    ):
        op.drop_column("accounts", name)
