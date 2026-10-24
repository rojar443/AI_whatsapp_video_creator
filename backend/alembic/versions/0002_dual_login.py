"""WhatsApp login: login_challenges table, nullable users.email / users.password_hash

Downgrade restores NOT NULL and therefore fails while WhatsApp-only users exist.

Revision ID: 0002
Revises: 0001
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_challenges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=10), nullable=False),
        sa.Column("wa_id", sa.String(length=20), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("poll_secret_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_login_challenges_wa_id", "login_challenges", ["wa_id"])
    with op.batch_alter_table("users") as batch:
        batch.alter_column("email", existing_type=sa.String(length=320), nullable=True)
        batch.alter_column("password_hash", existing_type=sa.String(length=255), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("password_hash", existing_type=sa.String(length=255), nullable=False)
        batch.alter_column("email", existing_type=sa.String(length=320), nullable=False)
    op.drop_index("ix_login_challenges_wa_id", table_name="login_challenges")
    op.drop_table("login_challenges")
