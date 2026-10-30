"""KB documents scoped by direction + content hash.

Revision ID: 015_kb_direction
Revises: 014_direction_group
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "015_kb_direction"
down_revision = "014_direction_group"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "kb_documents" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("kb_documents")}
    if "direction_id" not in cols:
        op.add_column(
            "kb_documents",
            sa.Column("direction_id", sa.String(36), sa.ForeignKey("research_directions.id"), nullable=True),
        )
        op.create_index("ix_kb_documents_direction_id", "kb_documents", ["direction_id"])
    if "content_hash" not in cols:
        op.add_column("kb_documents", sa.Column("content_hash", sa.String(64), nullable=False, server_default=""))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "kb_documents" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("kb_documents")}
    indexes = {i["name"] for i in insp.get_indexes("kb_documents")}
    if "ix_kb_documents_direction_id" in indexes:
        op.drop_index("ix_kb_documents_direction_id", table_name="kb_documents")
    if "direction_id" in cols:
        op.drop_column("kb_documents", "direction_id")
    if "content_hash" in cols:
        op.drop_column("kb_documents", "content_hash")
