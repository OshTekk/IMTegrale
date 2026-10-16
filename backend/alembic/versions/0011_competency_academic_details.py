"""Import semester and grade details from COMPETENCES.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ue_settings", sa.Column("official_code", sa.String(80)))
    op.add_column("ue_settings", sa.Column("semester", sa.String(16)))
    op.add_column("ue_settings", sa.Column("official_grade", sa.String(4)))
    op.add_column("ue_settings", sa.Column("earned_credits_ects", sa.Float()))
    op.execute(
        sa.text(
            "UPDATE notes SET label_override = NULL, score_override = NULL, "
            "coefficient_override = NULL, is_resit_override = NULL, "
            "hidden_by_user = FALSE WHERE source = 'pass'"
        )
    )


def downgrade() -> None:
    op.drop_column("ue_settings", "earned_credits_ects")
    op.drop_column("ue_settings", "official_grade")
    op.drop_column("ue_settings", "semester")
    op.drop_column("ue_settings", "official_code")
