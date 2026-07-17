"""Official PASS identity and Telegram delivery checks.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("telegram_last_test_at", sa.DateTime(timezone=True)))
    op.add_column("accounts", sa.Column("telegram_last_test_status", sa.String(16)))
    op.add_column("accounts", sa.Column("official_first_name", sa.String(120)))
    op.add_column("accounts", sa.Column("official_last_name", sa.String(120)))
    op.add_column("accounts", sa.Column("official_identity_at", sa.DateTime(timezone=True)))

    # A pseudonymous consent cannot authorize publication of an official identity.
    op.execute(
        sa.text(
            "UPDATE leaderboard_profiles SET "
            "is_participating = false, pseudonym = NULL, pseudonym_key = NULL, "
            "joined_at = NULL, ranking_visible_at = NULL, left_at = CURRENT_TIMESTAMP, "
            "rejoin_after = NULL, consent_version = NULL, consent_at = NULL "
            "WHERE is_participating = true OR pseudonym IS NOT NULL OR consent_at IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE accounts SET profile_refresh_requested_at = CURRENT_TIMESTAMP "
            "WHERE official_first_name IS NULL OR official_last_name IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("accounts", "official_identity_at")
    op.drop_column("accounts", "official_last_name")
    op.drop_column("accounts", "official_first_name")
    op.drop_column("accounts", "telegram_last_test_status")
    op.drop_column("accounts", "telegram_last_test_at")
