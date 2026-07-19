"""Add private Parcours access, progress, and attempt state.

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deliberately nullable and without a backfill: only a successful IMT/CAS
    # authentication is allowed to establish this independent proof of status.
    op.add_column(
        "accounts",
        sa.Column("student_status_verified_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "learning_access_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("audience", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(240), nullable=False),
        sa.Column(
            "granted_by_admin_id",
            sa.String(36),
            sa.ForeignKey("admin_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "length(trim(audience)) BETWEEN 1 AND 64",
            name="ck_learning_access_grants_audience",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) BETWEEN 1 AND 240",
            name="ck_learning_access_grants_reason",
        ),
        sa.CheckConstraint(
            "expires_at > granted_at",
            name="ck_learning_access_grants_expiry",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name="ck_learning_access_grants_revocation",
        ),
    )
    op.create_index(
        "ix_learning_access_grants_account_id",
        "learning_access_grants",
        ["account_id"],
    )
    op.create_index(
        "ix_learning_access_grants_granted_by_admin_id",
        "learning_access_grants",
        ["granted_by_admin_id"],
    )
    op.create_index(
        "ix_learning_access_grants_account_audience_revoked_expires",
        "learning_access_grants",
        ["account_id", "audience", "revoked_at", "expires_at"],
    )
    op.create_index(
        "ix_learning_access_grants_revoked_expires",
        "learning_access_grants",
        ["revoked_at", "expires_at"],
    )

    op.create_table(
        "learning_progress",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("audience", sa.String(64), nullable=False),
        sa.Column("content_id", sa.String(128), nullable=False),
        sa.Column("last_section_id", sa.String(128)),
        sa.Column("last_page", sa.Integer()),
        sa.Column(
            "completed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "exercise_viewed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "opened_hint_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("self_assessment", sa.Integer()),
        sa.Column(
            "favorite",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "length(trim(audience)) BETWEEN 1 AND 64",
            name="ck_learning_progress_audience",
        ),
        sa.CheckConstraint(
            "length(trim(content_id)) BETWEEN 1 AND 128",
            name="ck_learning_progress_content_id",
        ),
        sa.CheckConstraint(
            "last_section_id IS NULL OR "
            "length(trim(last_section_id)) BETWEEN 1 AND 128",
            name="ck_learning_progress_last_section_id",
        ),
        sa.CheckConstraint(
            "last_page IS NULL OR (last_page >= 1 AND last_page <= 100000)",
            name="ck_learning_progress_last_page",
        ),
        sa.CheckConstraint(
            "self_assessment IS NULL OR self_assessment BETWEEN 1 AND 5",
            name="ck_learning_progress_self_assessment",
        ),
        sa.UniqueConstraint(
            "account_id",
            "audience",
            "content_id",
            name="uq_learning_progress_account_audience_content",
        ),
    )
    op.create_index(
        "ix_learning_progress_account_id",
        "learning_progress",
        ["account_id"],
    )
    op.create_index(
        "ix_learning_progress_account_audience_updated",
        "learning_progress",
        ["account_id", "audience", "updated_at"],
    )

    op.create_table(
        "learning_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("audience", sa.String(64), nullable=False),
        sa.Column("exercise_id", sa.String(128), nullable=False),
        sa.Column("attempt_kind", sa.String(24), nullable=False),
        sa.Column("hint_id", sa.String(128)),
        sa.Column("self_assessment", sa.Integer()),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "length(trim(audience)) BETWEEN 1 AND 64",
            name="ck_learning_attempts_audience",
        ),
        sa.CheckConstraint(
            "length(trim(exercise_id)) BETWEEN 1 AND 128",
            name="ck_learning_attempts_exercise_id",
        ),
        sa.CheckConstraint(
            "attempt_kind IN ('viewed', 'hint_opened', 'self_assessed', 'completed')",
            name="ck_learning_attempts_kind",
        ),
        sa.CheckConstraint(
            "self_assessment IS NULL OR self_assessment BETWEEN 1 AND 5",
            name="ck_learning_attempts_self_assessment",
        ),
        sa.CheckConstraint(
            "(attempt_kind = 'hint_opened' AND hint_id IS NOT NULL AND "
            "length(trim(hint_id)) BETWEEN 1 AND 128) "
            "OR (attempt_kind <> 'hint_opened' AND hint_id IS NULL)",
            name="ck_learning_attempts_hint_payload",
        ),
        sa.CheckConstraint(
            "(attempt_kind = 'self_assessed' AND self_assessment IS NOT NULL) "
            "OR (attempt_kind <> 'self_assessed' AND self_assessment IS NULL)",
            name="ck_learning_attempts_assessment_payload",
        ),
    )
    op.create_index(
        "ix_learning_attempts_account_id",
        "learning_attempts",
        ["account_id"],
    )
    op.create_index(
        "ix_learning_attempts_account_audience_exercise_at",
        "learning_attempts",
        ["account_id", "audience", "exercise_id", "attempted_at"],
    )
    op.create_index(
        "ix_learning_attempts_account_attempted_at",
        "learning_attempts",
        ["account_id", "attempted_at"],
    )


def downgrade() -> None:
    op.drop_table("learning_attempts")
    op.drop_table("learning_progress")
    op.drop_table("learning_access_grants")
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("student_status_verified_at")
