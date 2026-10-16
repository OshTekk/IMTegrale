"""Import official UE metadata from COMPETENCES.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("ue_metadata_refreshed_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "accounts",
        sa.Column("ue_metadata_refresh_requested_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "ue_settings",
        sa.Column("metadata_source", sa.String(24), nullable=False, server_default="manual"),
    )
    op.add_column(
        "ue_settings",
        sa.Column("metadata_refreshed_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        sa.text(
            "UPDATE accounts SET ue_metadata_refresh_requested_at = CURRENT_TIMESTAMP"
        )
    )


def downgrade() -> None:
    op.drop_column("ue_settings", "metadata_refreshed_at")
    op.drop_column("ue_settings", "metadata_source")
    op.drop_column("accounts", "ue_metadata_refresh_requested_at")
    op.drop_column("accounts", "ue_metadata_refreshed_at")
