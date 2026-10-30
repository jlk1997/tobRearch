"""Degree milestones hang on direction + manuscript.

Revision ID: 008_degree_milestones
Revises: 007_llm_purpose
Create Date: 2026-09-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "008_degree_milestones"
down_revision = "007_llm_purpose"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "degree_milestones" in insp.get_table_names():
        return
    op.create_table(
        "degree_milestones",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lab_id", sa.String(36), sa.ForeignKey("labs.id"), nullable=False, index=True),
        sa.Column("direction_id", sa.String(36), sa.ForeignKey("research_directions.id"), nullable=False, index=True),
        sa.Column("manuscript_id", sa.String(36), sa.ForeignKey("manuscripts.id"), nullable=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(20), nullable=False, server_default="proposal"),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_degree_milestones_dir_created", "degree_milestones", ["direction_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "degree_milestones" not in insp.get_table_names():
        return
    op.drop_index("ix_degree_milestones_dir_created", table_name="degree_milestones")
    op.drop_table("degree_milestones")
