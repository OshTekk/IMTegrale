"""Add private GPA simulation scenarios.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "simulation_scenarios",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("created_from", sa.String(16), nullable=False, server_default="blank"),
        sa.Column("formula_version", sa.String(32), nullable=False, server_default="gpa-ects-v1"),
        sa.Column("source_revision", sa.String(64)),
        sa.Column("source_captured_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "created_from IN ('blank', 'academic')",
            name="ck_simulation_scenarios_created_from",
        ),
        sa.CheckConstraint("version >= 1", name="ck_simulation_scenarios_version"),
    )
    op.create_index(
        "ix_simulation_scenarios_account_id",
        "simulation_scenarios",
        ["account_id"],
    )
    op.create_index(
        "ix_simulation_scenarios_account_updated",
        "simulation_scenarios",
        ["account_id", "updated_at"],
    )

    op.create_table(
        "simulation_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.String(36),
            sa.ForeignKey("simulation_scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lineage_key", sa.String(80), nullable=False),
        sa.Column("source_ue_code", sa.String(32)),
        sa.Column("origin", sa.String(16), nullable=False, server_default="simulated"),
        sa.Column("source_status", sa.String(16), nullable=False, server_default="current"),
        sa.Column("semester", sa.String(16)),
        sa.Column("ue_code", sa.String(32)),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("credits_ects", sa.Numeric(6, 2)),
        sa.Column("grade", sa.String(4)),
        sa.Column("base_semester", sa.String(16)),
        sa.Column("base_ue_code", sa.String(32)),
        sa.Column("base_title", sa.String(200)),
        sa.Column("base_credits_ects", sa.Numeric(6, 2)),
        sa.Column("base_grade", sa.String(4)),
        sa.Column("base_grade_source", sa.String(24)),
        sa.Column("source_observed_at", sa.DateTime(timezone=True)),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "origin IN ('imported', 'simulated')",
            name="ck_simulation_entries_origin",
        ),
        sa.CheckConstraint(
            "source_status IN ('current', 'conflict', 'unavailable')",
            name="ck_simulation_entries_source_status",
        ),
        sa.CheckConstraint(
            "grade IS NULL OR grade IN ('A', 'B', 'C', 'D', 'E', 'FX', 'F')",
            name="ck_simulation_entries_grade",
        ),
        sa.CheckConstraint(
            "base_grade IS NULL OR base_grade IN ('A', 'B', 'C', 'D', 'E', 'FX', 'F')",
            name="ck_simulation_entries_base_grade",
        ),
        sa.CheckConstraint(
            "credits_ects IS NULL OR (credits_ects > 0 AND credits_ects <= 60)",
            name="ck_simulation_entries_credits",
        ),
        sa.CheckConstraint(
            "base_credits_ects IS NULL OR (base_credits_ects > 0 AND base_credits_ects <= 60)",
            name="ck_simulation_entries_base_credits",
        ),
        sa.UniqueConstraint(
            "scenario_id",
            "lineage_key",
            name="uq_simulation_entries_lineage",
        ),
    )
    op.create_index(
        "ix_simulation_entries_scenario_id",
        "simulation_entries",
        ["scenario_id"],
    )
    op.create_index(
        "ix_simulation_entries_scenario_position",
        "simulation_entries",
        ["scenario_id", "position"],
    )


def downgrade() -> None:
    op.drop_table("simulation_entries")
    op.drop_table("simulation_scenarios")
