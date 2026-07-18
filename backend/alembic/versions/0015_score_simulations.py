"""Add private score simulation scenarios.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("simulation_scenarios") as batch_op:
        batch_op.add_column(
            sa.Column("kind", sa.String(16), nullable=False, server_default="gpa")
        )
        batch_op.create_check_constraint(
            "ck_simulation_scenarios_kind",
            "kind IN ('gpa', 'notes')",
        )
    op.create_index(
        "ix_simulation_scenarios_account_kind_updated",
        "simulation_scenarios",
        ["account_id", "kind", "updated_at"],
    )

    op.create_table(
        "score_simulation_ues",
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
        sa.Column("base_semester", sa.String(16)),
        sa.Column("base_ue_code", sa.String(32)),
        sa.Column("base_title", sa.String(200)),
        sa.Column("base_credits_ects", sa.Numeric(6, 2)),
        sa.Column("source_observed_at", sa.DateTime(timezone=True)),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "origin IN ('imported', 'simulated')",
            name="ck_score_simulation_ues_origin",
        ),
        sa.CheckConstraint(
            "source_status IN ('current', 'conflict', 'unavailable')",
            name="ck_score_simulation_ues_source_status",
        ),
        sa.CheckConstraint(
            "credits_ects IS NULL OR (credits_ects > 0 AND credits_ects <= 60)",
            name="ck_score_simulation_ues_credits",
        ),
        sa.CheckConstraint(
            "base_credits_ects IS NULL OR (base_credits_ects > 0 AND base_credits_ects <= 60)",
            name="ck_score_simulation_ues_base_credits",
        ),
        sa.UniqueConstraint(
            "scenario_id",
            "lineage_key",
            name="uq_score_simulation_ues_lineage",
        ),
    )
    op.create_index(
        "ix_score_simulation_ues_scenario_id",
        "score_simulation_ues",
        ["scenario_id"],
    )
    op.create_index(
        "ix_score_simulation_ues_scenario_position",
        "score_simulation_ues",
        ["scenario_id", "position"],
    )

    op.create_table(
        "score_simulation_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "ue_id",
            sa.String(36),
            sa.ForeignKey("score_simulation_ues.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lineage_key", sa.String(120), nullable=False),
        sa.Column("source_note_key", sa.String(96)),
        sa.Column("origin", sa.String(16), nullable=False, server_default="simulated"),
        sa.Column("source_status", sa.String(16), nullable=False, server_default="current"),
        sa.Column("label", sa.String(240), nullable=False, server_default=""),
        sa.Column("score", sa.Numeric(5, 2)),
        sa.Column("coefficient", sa.Numeric(6, 2), nullable=False, server_default="1"),
        sa.Column("is_resit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("base_label", sa.String(240)),
        sa.Column("base_score", sa.Numeric(5, 2)),
        sa.Column("base_coefficient", sa.Numeric(6, 2)),
        sa.Column("base_is_resit", sa.Boolean()),
        sa.Column("source_observed_at", sa.DateTime(timezone=True)),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "origin IN ('imported', 'simulated')",
            name="ck_score_simulation_assessments_origin",
        ),
        sa.CheckConstraint(
            "source_status IN ('current', 'conflict', 'unavailable')",
            name="ck_score_simulation_assessments_source_status",
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 20)",
            name="ck_score_simulation_assessments_score",
        ),
        sa.CheckConstraint(
            "base_score IS NULL OR (base_score >= 0 AND base_score <= 20)",
            name="ck_score_simulation_assessments_base_score",
        ),
        sa.CheckConstraint(
            "coefficient > 0 AND coefficient <= 100",
            name="ck_score_simulation_assessments_coefficient",
        ),
        sa.CheckConstraint(
            "base_coefficient IS NULL OR (base_coefficient > 0 AND base_coefficient <= 100)",
            name="ck_score_simulation_assessments_base_coefficient",
        ),
        sa.UniqueConstraint(
            "ue_id",
            "lineage_key",
            name="uq_score_simulation_assessments_lineage",
        ),
    )
    op.create_index(
        "ix_score_simulation_assessments_ue_id",
        "score_simulation_assessments",
        ["ue_id"],
    )
    op.create_index(
        "ix_score_simulation_assessments_ue_position",
        "score_simulation_assessments",
        ["ue_id", "position"],
    )


def downgrade() -> None:
    op.drop_table("score_simulation_assessments")
    op.drop_table("score_simulation_ues")
    op.drop_index(
        "ix_simulation_scenarios_account_kind_updated",
        table_name="simulation_scenarios",
    )
    with op.batch_alter_table("simulation_scenarios") as batch_op:
        batch_op.drop_constraint("ck_simulation_scenarios_kind", type_="check")
        batch_op.drop_column("kind")
