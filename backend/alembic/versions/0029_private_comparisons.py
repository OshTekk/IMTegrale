"""Add private comparisons and opaque public event cursors.

Revision ID: 0029
Revises: 0028
"""

import secrets

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

EVENT_CURSOR_BACKFILL_MAX_ATTEMPTS = 16
EVENT_VISIBILITY_CLASSES = ("shared", "owner", "primary_owner", "simulation")
EVENT_VISIBILITY_BY_PREFIX = (
    ("private_comparison:", "primary_owner"),
    ("simulation:", "simulation"),
    ("account:", "owner"),
    ("auth:", "owner"),
    ("leaderboard:", "owner"),
    ("learning:", "owner"),
    ("telegram:", "owner"),
    ("token:", "owner"),
    ("calendar:", "shared"),
    ("note:", "shared"),
    ("pass_access:", "shared"),
    ("pass_session:", "shared"),
    ("passkey:", "shared"),
    ("security_setup:", "shared"),
    ("sync:", "shared"),
    ("sync_credential:", "shared"),
    ("ue:", "shared"),
)


def _new_public_event_cursor(used: set[str]) -> str:
    for _attempt in range(EVENT_CURSOR_BACKFILL_MAX_ATTEMPTS):
        cursor = "evc1_" + secrets.token_urlsafe(24)
        if len(cursor) == 37 and cursor not in used:
            return cursor
    raise RuntimeError("0029 could not allocate a unique public event cursor")


def _visibility_class_for_existing_event(kind: str) -> str:
    for prefix, visibility_class in EVENT_VISIBILITY_BY_PREFIX:
        if kind.startswith(prefix):
            return visibility_class
    # Unknown historical families are backfilled to the least-disclosing class.
    return "primary_owner"


def _create_event_visibility_immutability_trigger() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_event_visibility_class_update()
            RETURNS trigger AS $$
            BEGIN
                IF NEW.kind IS DISTINCT FROM OLD.kind
                   OR NEW.visibility_class IS DISTINCT FROM OLD.visibility_class THEN
                    RAISE EXCEPTION 'event classification is immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_events_visibility_class_immutable
            BEFORE UPDATE OF kind, visibility_class ON events
            FOR EACH ROW
            EXECUTE FUNCTION reject_event_visibility_class_update()
            """
        )
    elif bind.dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_events_visibility_class_immutable
            BEFORE UPDATE OF kind, visibility_class ON events
            FOR EACH ROW
            WHEN NEW.kind <> OLD.kind
              OR NEW.visibility_class <> OLD.visibility_class
            BEGIN
                SELECT RAISE(ABORT, 'event classification is immutable');
            END
            """
        )


def _drop_event_visibility_immutability_trigger() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "trg_events_visibility_class_immutable ON events"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_event_visibility_class_update()")
    elif bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_events_visibility_class_immutable")


def _add_public_event_cursors_and_visibility() -> None:
    op.add_column(
        "events",
        sa.Column("public_cursor", sa.String(length=37), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("visibility_class", sa.String(length=16), nullable=True),
    )
    bind = op.get_bind()
    used: set[str] = set()
    events = bind.execute(sa.text("SELECT id, kind FROM events ORDER BY id"))
    for event_id, kind in events:
        cursor = _new_public_event_cursor(used)
        used.add(cursor)
        bind.execute(
            sa.text(
                "UPDATE events SET public_cursor = :public_cursor, "
                "visibility_class = :visibility_class "
                "WHERE id = :event_id"
            ),
            {
                "public_cursor": cursor,
                "visibility_class": _visibility_class_for_existing_event(kind),
                "event_id": event_id,
            },
        )
    with op.batch_alter_table("events") as batch:
        batch.alter_column(
            "public_cursor",
            existing_type=sa.String(length=37),
            nullable=False,
        )
        batch.create_check_constraint(
            "ck_events_public_cursor",
            "length(public_cursor) = 37 "
            "AND substr(public_cursor, 1, 5) = 'evc1_'",
        )
        batch.alter_column(
            "visibility_class",
            existing_type=sa.String(length=16),
            nullable=False,
        )
        batch.create_check_constraint(
            "ck_events_visibility_class",
            "visibility_class IN ('shared', 'owner', 'primary_owner', 'simulation')",
        )
    op.create_index(
        "ix_events_public_cursor",
        "events",
        ["public_cursor"],
        unique=True,
    )
    op.create_index(
        "ix_events_account_visibility_id",
        "events",
        ["account_id", "visibility_class", "id"],
        unique=False,
    )
    _create_event_visibility_immutability_trigger()


def upgrade() -> None:
    _add_public_event_cursors_and_visibility()
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(
            sa.Column(
                "private_comparison_eligibility_generation",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "ck_accounts_private_comparison_eligibility_generation",
            "private_comparison_eligibility_generation >= 1",
        )
    op.create_table(
        "private_comparison_invitations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("creator_account_id", sa.String(length=36), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("creator_consent_manifest_digest", sa.String(length=64), nullable=False),
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
            "consent_version = 3",
            name="ck_private_comparison_invitations_consent_version",
        ),
        sa.CheckConstraint(
            "length(creator_consent_manifest_digest) = 64",
            name="ck_private_comparison_invitations_creator_manifest_digest",
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
            "revoked_reason IS NULL OR revoked_reason IN "
            "('creator_revoked', 'declined', 'operator_revoked', 'superseded_relation_cycle')",
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
        sa.Column("creator_account_id", sa.String(length=36), nullable=False),
        sa.Column("created_from_invitation_id", sa.String(length=36), nullable=True),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("creator_consent_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("acceptor_consent_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("account_a_eligibility_generation", sa.Integer(), nullable=False),
        sa.Column("account_b_eligibility_generation", sa.Integer(), nullable=False),
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
            "consent_version = 3",
            name="ck_private_comparisons_consent_version",
        ),
        sa.CheckConstraint(
            "creator_account_id IN (account_a_id, account_b_id)",
            name="ck_private_comparisons_creator_participant",
        ),
        sa.CheckConstraint(
            "length(creator_consent_manifest_digest) = 64 "
            "AND length(acceptor_consent_manifest_digest) = 64",
            name="ck_private_comparisons_manifest_digests",
        ),
        sa.CheckConstraint(
            "account_a_eligibility_generation >= 1 "
            "AND account_b_eligibility_generation >= 1",
            name="ck_private_comparisons_eligibility_generations",
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
            "(revoked_at IS NOT NULL AND revoked_reason IS NOT NULL AND "
            "((revoked_reason = 'eligibility_changed' AND revoked_by_account_id IS NULL) OR "
            "(revoked_reason <> 'eligibility_changed' AND revoked_by_account_id IS NOT NULL)))",
            name="ck_private_comparisons_revocation",
        ),
        sa.CheckConstraint(
            "revoked_by_account_id IS NULL OR revoked_by_account_id IN (account_a_id, account_b_id)",
            name="ck_private_comparisons_revoker_participant",
        ),
        sa.CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN "
            "('participant_revoked', 'operator_revoked', 'eligibility_changed')",
            name="ck_private_comparisons_revoked_reason",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= activated_at",
            name="ck_private_comparisons_revocation_order",
        ),
        sa.ForeignKeyConstraint(["account_a_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_b_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["creator_account_id"], ["accounts.id"], ondelete="CASCADE"),
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
    with op.batch_alter_table("accounts") as batch:
        batch.drop_constraint(
            "ck_accounts_private_comparison_eligibility_generation",
            type_="check",
        )
        batch.drop_column("private_comparison_eligibility_generation")
    _drop_event_visibility_immutability_trigger()
    op.drop_index("ix_events_account_visibility_id", table_name="events")
    op.drop_index("ix_events_public_cursor", table_name="events")
    with op.batch_alter_table("events") as batch:
        batch.drop_constraint("ck_events_visibility_class", type_="check")
        batch.drop_constraint("ck_events_public_cursor", type_="check")
        batch.drop_column("visibility_class")
        batch.drop_column("public_cursor")
