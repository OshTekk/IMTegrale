"""Add HPKE storage for PASS and HUB service sessions.

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

PASS_SERVICE_SESSION_ENVELOPE_BYTES = 65_652


def upgrade() -> None:
    with op.batch_alter_table("pass_service_sessions") as batch:
        batch.add_column(sa.Column("hpke_envelope", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("hpke_envelope_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("hpke_key_id", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("hpke_migrated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_check_constraint(
            "ck_pass_service_sessions_single_ciphertext",
            "encrypted_cookie_jar IS NULL OR hpke_envelope IS NULL",
        )
        batch.create_check_constraint(
            "ck_pass_service_sessions_hpke_metadata",
            "(hpke_envelope IS NULL AND hpke_envelope_version IS NULL "
            "AND hpke_key_id IS NULL AND hpke_migrated_at IS NULL) OR "
            "(hpke_envelope IS NOT NULL AND hpke_envelope_version IS NOT NULL "
            "AND hpke_key_id IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_pass_service_sessions_hpke_version",
            "hpke_envelope_version IS NULL OR hpke_envelope_version > 0",
        )
        batch.create_check_constraint(
            "ck_pass_service_sessions_hpke_key_id",
            "hpke_key_id IS NULL OR length(hpke_key_id) = 64",
        )
        batch.create_check_constraint(
            "ck_pass_service_sessions_hpke_size",
            "hpke_envelope IS NULL OR length(hpke_envelope) = "
            f"{PASS_SERVICE_SESSION_ENVELOPE_BYTES}",
        )
        batch.create_check_constraint(
            "ck_pass_service_sessions_active_ciphertext",
            "state != 'active' OR ("
            "(encrypted_cookie_jar IS NOT NULL AND hpke_envelope IS NULL) OR "
            "(encrypted_cookie_jar IS NULL AND hpke_envelope IS NOT NULL))",
        )
        batch.create_check_constraint(
            "ck_pass_service_sessions_inactive_no_ciphertext",
            "state = 'active' OR "
            "(encrypted_cookie_jar IS NULL AND hpke_envelope IS NULL)",
        )
        batch.create_index(
            "ix_pass_service_sessions_hpke_key_id",
            ["hpke_key_id"],
            unique=False,
        )


def downgrade() -> None:
    connection = op.get_bind()
    hpke_count = connection.execute(
        sa.text(
            "SELECT count(*) FROM pass_service_sessions "
            "WHERE hpke_envelope IS NOT NULL"
        )
    ).scalar_one()
    if hpke_count:
        raise RuntimeError(
            "0026 downgrade refused while HPKE service sessions exist; "
            "revoke them explicitly before schema rollback"
        )
    with op.batch_alter_table("pass_service_sessions") as batch:
        batch.drop_index("ix_pass_service_sessions_hpke_key_id")
        batch.drop_constraint(
            "ck_pass_service_sessions_inactive_no_ciphertext",
            type_="check",
        )
        batch.drop_constraint(
            "ck_pass_service_sessions_active_ciphertext",
            type_="check",
        )
        batch.drop_constraint("ck_pass_service_sessions_hpke_size", type_="check")
        batch.drop_constraint("ck_pass_service_sessions_hpke_key_id", type_="check")
        batch.drop_constraint("ck_pass_service_sessions_hpke_version", type_="check")
        batch.drop_constraint("ck_pass_service_sessions_hpke_metadata", type_="check")
        batch.drop_constraint(
            "ck_pass_service_sessions_single_ciphertext",
            type_="check",
        )
        batch.drop_column("hpke_migrated_at")
        batch.drop_column("hpke_key_id")
        batch.drop_column("hpke_envelope_version")
        batch.drop_column("hpke_envelope")
