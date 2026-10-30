"""Metadata-only project/revision usage attribution.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=True))
    with op.batch_alter_table("usage_records") as batch:
        batch.add_column(sa.Column("project_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("message_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("model", sa.String(120), nullable=True))
        batch.add_column(sa.Column("task", sa.String(40), nullable=True))
        batch.add_column(sa.Column("input_tokens", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("output_tokens", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cost_known", sa.Boolean(), nullable=True))
        batch.alter_column("estimated_cost_usd", existing_type=sa.Numeric(10, 4), type_=sa.Numeric(18, 10))
        batch.create_foreign_key("fk_usage_project", "video_projects", ["project_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_usage_message", "messages", ["message_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_usage_records_project_id", ["project_id"])
    # Only job-linked history has reliable attribution. Never infer links from timestamps.
    op.execute("""UPDATE usage_records SET
        project_id = (SELECT project_id FROM generation_jobs WHERE generation_jobs.id = usage_records.job_id),
        revision = (SELECT revision FROM generation_jobs WHERE generation_jobs.id = usage_records.job_id)
        WHERE job_id IS NOT NULL""")


def downgrade():
    with op.batch_alter_table("usage_records") as batch:
        batch.drop_index("ix_usage_records_project_id")
        batch.drop_constraint("fk_usage_project", type_="foreignkey")
        batch.drop_constraint("fk_usage_message", type_="foreignkey")
        for name in ("project_id", "message_id", "revision", "model", "task", "input_tokens", "output_tokens", "cost_known"):
            batch.drop_column(name)
        batch.alter_column("estimated_cost_usd", existing_type=sa.Numeric(18, 10), type_=sa.Numeric(10, 4))
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("revision")
