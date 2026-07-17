"""Make leaderboard score locking automatic.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "leaderboard_profiles",
        "score_ects_snapshot",
        new_column_name="score_ects_basis",
    )
    op.alter_column(
        "leaderboard_profiles",
        "score_verified_at",
        new_column_name="score_basis_updated_at",
    )
    op.drop_column("leaderboard_profiles", "score_verified_by_admin_id")

    profiles = sa.table(
        "leaderboard_profiles",
        sa.column("account_id", sa.String(length=36)),
        sa.column("is_participating", sa.Boolean()),
        sa.column("joined_at", sa.DateTime(timezone=True)),
        sa.column("ranking_visible_at", sa.DateTime(timezone=True)),
        sa.column("left_at", sa.DateTime(timezone=True)),
        sa.column("rejoin_after", sa.DateTime(timezone=True)),
        sa.column("consent_version", sa.String(length=32)),
        sa.column("consent_at", sa.DateTime(timezone=True)),
        sa.column("score_ects_basis", sa.JSON(none_as_null=True)),
        sa.column("score_basis_updated_at", sa.DateTime(timezone=True)),
    )
    connection = op.get_bind()

    # The publication timing and score provenance both changed. Existing consent
    # cannot authorize the new policy, regardless of whether the old snapshot was complete.
    connection.execute(
        sa.update(profiles)
        .where(profiles.c.is_participating.is_(True))
        .values(
            is_participating=False,
            joined_at=None,
            ranking_visible_at=None,
            left_at=sa.func.now(),
            rejoin_after=None,
        )
    )
    # Clear all legacy authorization and score material. Inactive users retain
    # their existing left_at/rejoin_after values, including an active cooldown.
    connection.execute(
        sa.update(profiles).values(
            consent_version=None,
            consent_at=None,
            score_ects_basis=sa.null(),
            score_basis_updated_at=None,
        )
    )


def downgrade() -> None:
    op.add_column(
        "leaderboard_profiles",
        sa.Column("score_verified_by_admin_id", sa.String(length=36), nullable=True),
    )
    op.alter_column(
        "leaderboard_profiles",
        "score_basis_updated_at",
        new_column_name="score_verified_at",
    )
    op.alter_column(
        "leaderboard_profiles",
        "score_ects_basis",
        new_column_name="score_ects_snapshot",
    )
