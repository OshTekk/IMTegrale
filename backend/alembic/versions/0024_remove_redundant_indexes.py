"""Remove indexes duplicated by PostgreSQL unique constraints.

Revision ID: 0024
Revises: 0023
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

_REDUNDANT_INDEXES = (
    ("accounts", "ix_accounts_imt_username", ("imt_username",), True),
    ("share_tokens", "ix_share_tokens_prefix", ("prefix",), True),
    ("web_sessions", "ix_web_sessions_digest", ("digest",), True),
    (
        "leaderboard_profiles",
        "ix_leaderboard_profiles_pseudonym_key",
        ("pseudonym_key",),
        False,
    ),
    ("admin_users", "ix_admin_users_username", ("username",), True),
    ("admin_sessions", "ix_admin_sessions_digest", ("digest",), True),
)


def upgrade() -> None:
    for table_name, index_name, _columns, _unique in _REDUNDANT_INDEXES:
        op.drop_index(index_name, table_name=table_name)


def downgrade() -> None:
    for table_name, index_name, columns, unique in _REDUNDANT_INDEXES:
        op.create_index(index_name, table_name, list(columns), unique=unique)
