"""Add private comparison invitations and bilateral relations.

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "private_comparison_invitations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("creator_account_id", sa.String(length=36), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("validity_days", sa.Integer(), server_default=sa.text("7"), nullable=False),
        sa.Column("relationship_duration_days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by_account_id", sa.String(length=36), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "length(public_id) = 28 AND substr(public_id, 1, 4) = 'pci_'",
            name="ck_private_comparison_invitations_public_id",
        ),
        sa.CheckConstraint(
            "length(token_digest) = 64",
            name="ck_private_comparison_invitations_token_digest",
        ),
        sa.CheckConstraint(
            "token_version = 1",
            name="ck_private_comparison_invitations_token_version",
        ),
        sa.CheckConstraint(
            "consent_version = 1",
            name="ck_private_comparison_invitations_consent_version",
        ),
        sa.CheckConstraint(
            "validity_days BETWEEN 1 AND 7",
            name="ck_private_comparison_invitations_validity_days",
        ),
        sa.CheckConstraint(
            "relationship_duration_days BETWEEN 1 AND 90",
            name="ck_private_comparison_invitations_relationship_duration",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_private_comparison_invitations_expiration",
        ),
        sa.CheckConstraint(
            "(consumed_at IS NULL) = (consumed_by_account_id IS NULL)",
            name="ck_private_comparison_invitations_consumption",
        ),
        sa.CheckConstraint(
            "consumed_by_account_id IS NULL OR consumed_by_account_id <> creator_account_id",
            name="ck_private_comparison_invitations_distinct_consumer",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR (consumed_at >= created_at AND consumed_at <= expires_at)",
            name="ck_private_comparison_invitations_consumption_order",
        ),
        sa.CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_private_comparison_invitations_terminal_state",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_reason IS NOT NULL)",
            name="ck_private_comparison_invitations_revocation",
        ),
        sa.CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN ('creator_revoked', 'declined', 'operator_revoked')",
            name="ck_private_comparison_invitations_revoked_reason",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_private_comparison_invitations_revocation_order",
        ),
        sa.ForeignKeyConstraint(
            ["consumed_by_account_id"],
            ["accounts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["creator_account_id"],
            ["accounts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "public_id",
            name="uq_private_comparison_invitations_public_id",
        ),
        sa.UniqueConstraint(
            "token_digest",
            name="uq_private_comparison_invitations_token_digest",
        ),
    )
    op.create_index(
        "ix_private_comparison_invitations_creator_created",
        "private_comparison_invitations",
        ["creator_account_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_private_comparison_invitations_expires_at",
        "private_comparison_invitations",
        ["expires_at"],
        unique=False,
    )

    op.create_table(
        "private_comparisons",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("account_a_id", sa.String(length=36), nullable=False),
        sa.Column("account_b_id", sa.String(length=36), nullable=False),
        sa.Column("created_from_invitation_id", sa.String(length=36), nullable=True),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("account_a_consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_b_consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_account_id", sa.String(length=36), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(public_id) = 27 AND substr(public_id, 1, 3) = 'pc_'",
            name="ck_private_comparisons_public_id",
        ),
        sa.CheckConstraint(
            "account_a_id < account_b_id",
            name="ck_private_comparisons_canonical_pair",
        ),
        sa.CheckConstraint(
            "consent_version = 1",
            name="ck_private_comparisons_consent_version",
        ),
        sa.CheckConstraint(
            "duration_days BETWEEN 1 AND 90",
            name="ck_private_comparisons_duration",
        ),
        sa.CheckConstraint(
            "expires_at > activated_at",
            name="ck_private_comparisons_expiration",
        ),
        sa.CheckConstraint(
            "created_at <= activated_at",
            name="ck_private_comparisons_activation_order",
        ),
        sa.CheckConstraint(
            "account_a_consented_at <= activated_at AND account_b_consented_at <= activated_at",
            name="ck_private_comparisons_consent_order",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_account_id IS NULL AND revoked_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by_account_id IS NOT NULL "
            "AND revoked_reason IS NOT NULL)",
            name="ck_private_comparisons_revocation",
        ),
        sa.CheckConstraint(
            "revoked_by_account_id IS NULL OR revoked_by_account_id IN (account_a_id, account_b_id)",
            name="ck_private_comparisons_revoker_participant",
        ),
        sa.CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN ('participant_revoked', 'operator_revoked')",
            name="ck_private_comparisons_revoked_reason",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= activated_at",
            name="ck_private_comparisons_revocation_order",
        ),
        sa.ForeignKeyConstraint(["account_a_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_b_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_from_invitation_id"],
            ["private_comparison_invitations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_account_id"],
            ["accounts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id", name="uq_private_comparisons_public_id"),
        sa.UniqueConstraint(
            "account_a_id",
            "account_b_id",
            name="uq_private_comparisons_account_pair",
        ),
    )
    op.create_index(
        "ix_private_comparisons_account_a",
        "private_comparisons",
        ["account_a_id"],
        unique=False,
    )
    op.create_index(
        "ix_private_comparisons_account_b",
        "private_comparisons",
        ["account_b_id"],
        unique=False,
    )
    op.create_index(
        "ix_private_comparisons_expires_at",
        "private_comparisons",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    invitations = int(
        bind.execute(sa.text("SELECT count(*) FROM private_comparison_invitations")).scalar_one()
    )
    comparisons = int(bind.execute(sa.text("SELECT count(*) FROM private_comparisons")).scalar_one())
    if invitations or comparisons:
        raise RuntimeError("0029 downgrade refused while private comparison data exists")

    op.drop_index("ix_private_comparisons_expires_at", table_name="private_comparisons")
    op.drop_index("ix_private_comparisons_account_b", table_name="private_comparisons")
    op.drop_index("ix_private_comparisons_account_a", table_name="private_comparisons")
    op.drop_table("private_comparisons")
    op.drop_index(
        "ix_private_comparison_invitations_expires_at",
        table_name="private_comparison_invitations",
    )
    op.drop_index(
        "ix_private_comparison_invitations_creator_created",
        table_name="private_comparison_invitations",
    )
    op.drop_table("private_comparison_invitations")
