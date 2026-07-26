"""Prepare the autonomous synchronization runtime.

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

PAUSE_REASONS = (
    "reauth_required",
    "credential_invalid",
    "credential_key_unavailable",
    "autonomous_runtime_unavailable",
)


def _pause_reason_check() -> str:
    return (
        "auto_sync_paused_reason IS NULL OR auto_sync_paused_reason IN ("
        + ", ".join(f"'{reason}'" for reason in PAUSE_REASONS)
        + ")"
    )


def upgrade() -> None:
    with op.batch_alter_table("pass_operations") as batch:
        batch.add_column(
            sa.Column(
                "autonomous_credential_used",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    # G4 used this operational pause for an unavailable session key. G6 uses
    # one bounded vocabulary and keeps the encrypted material recoverable.
    op.execute(
        sa.text(
            "UPDATE accounts "
            "SET auto_sync_paused_reason = 'credential_key_unavailable' "
            "WHERE auto_sync_paused_reason = 'key_unavailable'"
        )
    )
    with op.batch_alter_table("accounts") as batch:
        batch.create_check_constraint(
            "ck_accounts_auto_sync_paused_reason",
            _pause_reason_check(),
        )


def downgrade() -> None:
    unsupported = int(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM accounts "
                "WHERE auto_sync_paused_reason IN "
                "('credential_invalid', 'autonomous_runtime_unavailable')"
            )
        )
        .scalar_one()
    )
    used = int(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM pass_operations "
                "WHERE autonomous_credential_used = true"
            )
        )
        .scalar_one()
    )
    if unsupported or used:
        raise RuntimeError(
            "0028 downgrade refused after autonomous runtime state has been used"
        )

    with op.batch_alter_table("accounts") as batch:
        batch.drop_constraint(
            "ck_accounts_auto_sync_paused_reason",
            type_="check",
        )
    op.execute(
        sa.text(
            "UPDATE accounts "
            "SET auto_sync_paused_reason = 'key_unavailable' "
            "WHERE auto_sync_paused_reason = 'credential_key_unavailable'"
        )
    )
    with op.batch_alter_table("pass_operations") as batch:
        batch.drop_column("autonomous_credential_used")
