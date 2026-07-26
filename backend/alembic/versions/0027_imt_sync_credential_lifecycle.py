"""Harden the empty IMT sync credential lifecycle table.

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES = 3_172
REVOCATION_REASONS = (
    "user_revoked",
    "manual_mode",
    "session_only_mode",
    "pass_access_purged",
    "credential_replaced",
    "credential_invalid",
    "key_unavailable",
    "database_restored",
    "account_disabled",
    "login_changed",
    "operator_revoked",
)


def _row_count() -> int:
    return int(
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM imt_sync_credentials"))
        .scalar_one()
    )


def _revocation_reason_check() -> str:
    return (
        "revoked_reason IS NULL OR revoked_reason IN ("
        + ", ".join(f"'{reason}'" for reason in REVOCATION_REASONS)
        + ")"
    )


def upgrade() -> None:
    if _row_count() != 0:
        raise RuntimeError(
            "0027 upgrade refused: imt_sync_credentials must remain empty before G5"
        )

    with op.batch_alter_table("imt_sync_credentials") as batch:
        batch.drop_constraint(
            "ck_imt_sync_credentials_inactive_envelope",
            type_="check",
        )
        batch.drop_constraint(
            "ck_imt_sync_credentials_active_fields",
            type_="check",
        )
        batch.drop_constraint(
            "ck_imt_sync_credentials_revoked_reason",
            type_="check",
        )
        batch.drop_constraint(
            "ck_imt_sync_credentials_key_id_size",
            type_="check",
        )
        batch.drop_constraint(
            "ck_imt_sync_credentials_envelope_version",
            type_="check",
        )
        batch.drop_constraint(
            "ck_imt_sync_credentials_envelope_size",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_imt_sync_credentials_envelope_size",
            "encrypted_envelope IS NULL OR "
            f"length(encrypted_envelope) = {IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES}",
        )
        batch.create_check_constraint(
            "ck_imt_sync_credentials_envelope_version",
            "envelope_version IS NULL OR envelope_version >= 1",
        )
        batch.create_check_constraint(
            "ck_imt_sync_credentials_key_id_size",
            "key_id IS NULL OR length(key_id) = 64",
        )
        batch.create_check_constraint(
            "ck_imt_sync_credentials_revoked_reason",
            _revocation_reason_check(),
        )
        batch.create_check_constraint(
            "ck_imt_sync_credentials_active_fields",
            "state != 'active' OR ("
            "encrypted_envelope IS NOT NULL "
            f"AND length(encrypted_envelope) = {IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES} "
            "AND envelope_version IS NOT NULL "
            "AND envelope_version >= 1 "
            "AND key_id IS NOT NULL "
            "AND length(key_id) = 64 "
            "AND consent_version >= 1 "
            "AND consented_at IS NOT NULL "
            "AND verified_at IS NOT NULL "
            "AND revoked_at IS NULL "
            "AND revoked_reason IS NULL"
            ")",
        )
        batch.create_check_constraint(
            "ck_imt_sync_credentials_inactive_fields",
            "state = 'active' OR ("
            "encrypted_envelope IS NULL "
            "AND envelope_version IS NULL "
            "AND key_id IS NULL "
            "AND revoked_at IS NOT NULL "
            "AND revoked_reason IS NOT NULL"
            ")",
        )
        batch.create_index(
            "ix_imt_sync_credentials_key_id",
            ["key_id"],
            unique=False,
        )
        batch.create_index(
            "ix_imt_sync_credentials_state",
            ["state"],
            unique=False,
        )


def downgrade() -> None:
    if _row_count() != 0:
        raise RuntimeError(
            "0027 downgrade refused while IMT sync credential lifecycle rows exist"
        )

    with op.batch_alter_table("imt_sync_credentials") as batch:
        batch.drop_index("ix_imt_sync_credentials_state")
        batch.drop_index("ix_imt_sync_credentials_key_id")
        batch.drop_constraint(
            "ck_imt_sync_credentials_inactive_fields",
            type_="check",
        )
        batch.drop_constraint(
            "ck_imt_sync_credentials_active_fields",
            type_="check",
        )
        batch.drop_constraint(
            "ck_imt_sync_credentials_revoked_reason",
            type_="check",
        )
        batch.drop_constraint(
            "ck_imt_sync_credentials_key_id_size",
            type_="check",
        )
        batch.drop_constraint(
            "ck_imt_sync_credentials_envelope_version",
            type_="check",
        )
        batch.drop_constraint(
            "ck_imt_sync_credentials_envelope_size",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_imt_sync_credentials_envelope_size",
            "encrypted_envelope IS NULL "
            "OR length(encrypted_envelope) BETWEEN 48 AND 4096",
        )
        batch.create_check_constraint(
            "ck_imt_sync_credentials_envelope_version",
            "envelope_version IS NULL OR envelope_version >= 1",
        )
        batch.create_check_constraint(
            "ck_imt_sync_credentials_key_id_size",
            "key_id IS NULL OR length(key_id) BETWEEN 1 AND 64",
        )
        batch.create_check_constraint(
            "ck_imt_sync_credentials_revoked_reason",
            "revoked_reason IS NULL OR revoked_reason IN ("
            "'manual_mode', 'session_only_mode', 'pass_access_purged', "
            "'credential_replaced', 'credential_invalid', 'key_unavailable'"
            ")",
        )
        batch.create_check_constraint(
            "ck_imt_sync_credentials_active_fields",
            "state != 'active' OR ("
            "encrypted_envelope IS NOT NULL "
            "AND envelope_version >= 1 "
            "AND key_id IS NOT NULL "
            "AND verified_at IS NOT NULL"
            ")",
        )
        batch.create_check_constraint(
            "ck_imt_sync_credentials_inactive_envelope",
            "state = 'active' OR encrypted_envelope IS NULL",
        )
