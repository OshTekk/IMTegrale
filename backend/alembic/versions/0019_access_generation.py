"""Fence access issuance against concurrent revocation.

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def _generation_column() -> sa.Column:
    return sa.Column(
        "access_generation",
        sa.Integer(),
        nullable=False,
        server_default=sa.text("1"),
    )


def upgrade() -> None:
    op.add_column("accounts", _generation_column())
    op.add_column("passkey_credentials", _generation_column())
    op.add_column("share_tokens", _generation_column())
    op.add_column("web_sessions", _generation_column())


def downgrade() -> None:
    op.drop_column("web_sessions", "access_generation")
    op.drop_column("share_tokens", "access_generation")
    op.drop_column("passkey_credentials", "access_generation")
    op.drop_column("accounts", "access_generation")
