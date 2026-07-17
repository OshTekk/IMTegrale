"""Freeze and verify ECTS used by the public leaderboard.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leaderboard_profiles", sa.Column("score_ects_snapshot", sa.JSON()))
    op.add_column(
        "leaderboard_profiles",
        sa.Column("score_verified_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "leaderboard_profiles",
        sa.Column("score_verified_by_admin_id", sa.String(36)),
    )


def downgrade() -> None:
    op.drop_column("leaderboard_profiles", "score_verified_by_admin_id")
    op.drop_column("leaderboard_profiles", "score_verified_at")
    op.drop_column("leaderboard_profiles", "score_ects_snapshot")
