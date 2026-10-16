"""Store academic quantities as deterministic decimals.

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def _to_numeric(
    table: str,
    column: str,
    precision: int,
    scale: int,
    *,
    nullable: bool,
) -> None:
    op.alter_column(
        table,
        column,
        existing_type=sa.Float(),
        type_=sa.Numeric(precision, scale),
        existing_nullable=nullable,
        postgresql_using=f"ROUND({column}::numeric, {scale})",
    )


def _to_float(
    table: str,
    column: str,
    precision: int,
    scale: int,
    *,
    nullable: bool,
) -> None:
    op.alter_column(
        table,
        column,
        existing_type=sa.Numeric(precision, scale),
        type_=sa.Float(),
        existing_nullable=nullable,
        postgresql_using=f"{column}::double precision",
    )


def upgrade() -> None:
    _to_numeric("notes", "raw_score", 5, 2, nullable=False)
    _to_numeric("notes", "raw_coefficient", 7, 3, nullable=False)
    _to_numeric("notes", "score_override", 5, 2, nullable=True)
    _to_numeric("notes", "coefficient_override", 7, 3, nullable=True)
    _to_numeric("ue_settings", "credits_ects", 6, 2, nullable=True)
    _to_numeric("ue_settings", "earned_credits_ects", 6, 2, nullable=True)


def downgrade() -> None:
    _to_float("ue_settings", "earned_credits_ects", 6, 2, nullable=True)
    _to_float("ue_settings", "credits_ects", 6, 2, nullable=True)
    _to_float("notes", "coefficient_override", 7, 3, nullable=True)
    _to_float("notes", "score_override", 5, 2, nullable=True)
    _to_float("notes", "raw_coefficient", 7, 3, nullable=False)
    _to_float("notes", "raw_score", 5, 2, nullable=False)
