"""Normalize engineering semesters to S5 through S10.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


_TO_ENGINEERING = """
CASE {column}
    WHEN 'S1' THEN 'S5'
    WHEN 'S2' THEN 'S6'
    WHEN 'S3' THEN 'S7'
    WHEN 'S4' THEN 'S8'
    WHEN 'S5' THEN 'S9'
    WHEN 'S6' THEN 'S10'
    ELSE {column}
END
"""

_TO_LEGACY = """
CASE {column}
    WHEN 'S5' THEN 'S1'
    WHEN 'S6' THEN 'S2'
    WHEN 'S7' THEN 'S3'
    WHEN 'S8' THEN 'S4'
    WHEN 'S9' THEN 'S5'
    WHEN 'S10' THEN 'S6'
    ELSE {column}
END
"""


def _canonical_semester(column: str, title_column: str) -> str:
    padded_title = f"(' ' || UPPER(COALESCE({title_column}, '')) || ' ')"
    return f"""
CASE
    WHEN {padded_title} LIKE '% S10 %' THEN 'S10'
    WHEN {padded_title} LIKE '% S9 %' THEN 'S9'
    WHEN {padded_title} LIKE '% S8 %' THEN 'S8'
    WHEN {padded_title} LIKE '% S7 %' THEN 'S7'
    WHEN {padded_title} LIKE '% S6 %' THEN 'S6'
    WHEN {padded_title} LIKE '% S5 %' THEN 'S5'
    ELSE {_TO_ENGINEERING.format(column=column)}
END
"""


def upgrade() -> None:
    op.add_column("ue_settings", sa.Column("source_semester", sa.String(32)))
    op.execute("UPDATE ue_settings SET source_semester = semester WHERE semester IS NOT NULL")
    op.execute(
        f"""
        UPDATE ue_settings
        SET semester = {_canonical_semester('semester', 'title')}
        WHERE metadata_source = 'competences' AND semester IN ('S1', 'S2', 'S3', 'S4', 'S5', 'S6')
        """
    )
    op.execute(
        f"""
        UPDATE ue_settings
        SET semester = {_TO_ENGINEERING.format(column='semester')}
        WHERE metadata_source != 'competences' AND semester IN ('S1', 'S2', 'S3', 'S4')
        """
    )

    op.execute(
        f"""
        UPDATE simulation_entries
        SET semester = {_canonical_semester('semester', 'base_title')}
        WHERE origin = 'imported'
          AND semester = base_semester
          AND base_semester IN ('S1', 'S2', 'S3', 'S4', 'S5', 'S6')
        """
    )
    op.execute(
        f"""
        UPDATE simulation_entries
        SET base_semester = {_canonical_semester('base_semester', 'base_title')}
        WHERE origin = 'imported' AND base_semester IN ('S1', 'S2', 'S3', 'S4', 'S5', 'S6')
        """
    )
    op.execute(
        f"""
        UPDATE simulation_entries
        SET semester = {_TO_ENGINEERING.format(column='semester')}
        WHERE origin = 'imported' AND semester IN ('S1', 'S2', 'S3', 'S4')
        """
    )
    op.execute(
        f"""
        UPDATE simulation_entries
        SET semester = {_TO_ENGINEERING.format(column='semester')}
        WHERE origin = 'simulated' AND semester IN ('S1', 'S2', 'S3', 'S4')
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE simulation_entries
        SET semester = {_TO_LEGACY.format(column='semester')}
        WHERE origin = 'imported'
          AND semester = base_semester
          AND base_semester IN ('S5', 'S6', 'S7', 'S8', 'S9', 'S10')
        """
    )
    op.execute(
        f"""
        UPDATE simulation_entries
        SET base_semester = {_TO_LEGACY.format(column='base_semester')}
        WHERE origin = 'imported' AND base_semester IN ('S5', 'S6', 'S7', 'S8', 'S9', 'S10')
        """
    )
    op.execute(
        f"""
        UPDATE ue_settings
        SET semester = {_TO_LEGACY.format(column='semester')}
        WHERE metadata_source = 'competences' AND semester IN ('S5', 'S6', 'S7', 'S8', 'S9', 'S10')
        """
    )
    op.drop_column("ue_settings", "source_semester")
