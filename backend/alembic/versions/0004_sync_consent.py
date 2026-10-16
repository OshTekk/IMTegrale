"""Per-account automatic sync consent and official PASS campus.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("auto_sync_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "accounts",
        sa.Column("auto_sync_interval_hours", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column("accounts", sa.Column("auto_sync_consented_at", sa.DateTime(timezone=True)))
    if op.get_bind().dialect.name != "sqlite":
        op.create_check_constraint(
            "ck_accounts_auto_sync_interval",
            "accounts",
            "auto_sync_interval_hours IN (2, 4, 6, 8, 12, 24)",
        )
    op.execute(
        sa.text(
            """
            UPDATE accounts
            SET campus = detected_campus,
                campus_source = 'pass',
                campus_confirmed_at = detected_campus_at,
                classification_review_required = false
            WHERE detected_campus IN ('rennes', 'brest', 'nantes', 'other')
              AND campus_source <> 'admin'
            """
        )
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("ck_accounts_auto_sync_interval", "accounts", type_="check")
    op.drop_column("accounts", "auto_sync_consented_at")
    op.drop_column("accounts", "auto_sync_interval_hours")
    op.drop_column("accounts", "auto_sync_enabled")
