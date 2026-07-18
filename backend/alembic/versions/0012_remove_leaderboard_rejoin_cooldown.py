"""Remove the obsolete leaderboard rejoin cooldown.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE leaderboard_profiles SET rejoin_after = NULL"))


def downgrade() -> None:
    # Previous cooldown timestamps cannot be reconstructed after removal.
    pass
