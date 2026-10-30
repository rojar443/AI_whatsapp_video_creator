"""Persist AI fallback diagnostics in the operational ledger."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("usage_records") as batch:
        batch.add_column(sa.Column("diagnostic", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("usage_records") as batch:
        batch.drop_column("diagnostic")
