"""Reconcile the operational singleton sequence without advancing it.

Revision ID: 0029
Revises: 0028
"""

from alembic import op
from app.operational_sequence_reconciliation_v1 import (
    RECONCILIATION_REVISION,
    reconcile_operational_sequence,
)

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    reconcile_operational_sequence(
        op.get_bind(),
        expected_revision=RECONCILIATION_REVISION,
    )


def downgrade() -> None:
    # The reconciliation is intentionally monotonic.  Returning to the 0028
    # runtime must never restore is_called=false or lower the sequence value.
    pass
