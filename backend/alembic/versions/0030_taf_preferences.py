"""Add participant-owned ordered TAF wishes.

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

TAF_CODES = (
    "COUAD",
    "Cyber",
    "Dasci",
    "HEALTH",
    "ILSD",
    "NetCloud",
    "OPE",
    "SEH",
    "STAR",
    "TERO",
)


def upgrade() -> None:
    op.create_table(
        "taf_preferences",
        sa.Column(
            "account_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column("taf_code", sa.String(length=16), nullable=False),
        sa.Column("preference_rank", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_taf_preferences_account_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "account_id",
            "taf_code",
            name="pk_taf_preferences",
        ),
        sa.UniqueConstraint(
            "account_id",
            "preference_rank",
            name="uq_taf_preferences_account_rank",
        ),
        sa.CheckConstraint(
            "preference_rank >= 1 AND preference_rank <= 10",
            name="ck_taf_preferences_rank",
        ),
        sa.CheckConstraint(
            "taf_code IN ("
            + ", ".join(repr(code) for code in TAF_CODES)
            + ")",
            name="ck_taf_preferences_code",
        ),
    )


def downgrade() -> None:
    op.drop_table("taf_preferences")
