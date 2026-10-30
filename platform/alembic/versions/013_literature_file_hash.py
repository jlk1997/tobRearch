"""Literature file content hash for upload dedupe.

Revision ID: 013_literature_file_hash
Revises: 012_literature_core
Create Date: 2026-09-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "013_literature_file_hash"
down_revision = "012_literature_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "literature_files" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("literature_files")}
    if "content_sha256" not in existing:
        op.add_column("literature_files", sa.Column("content_sha256", sa.String(64), nullable=True))
    indexes = {i["name"] for i in insp.get_indexes("literature_files")}
    if "ix_literature_files_sha256" not in indexes:
        op.create_index("ix_literature_files_sha256", "literature_files", ["content_sha256"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "literature_files" not in insp.get_table_names():
        return
    indexes = {i["name"] for i in insp.get_indexes("literature_files")}
    if "ix_literature_files_sha256" in indexes:
        op.drop_index("ix_literature_files_sha256", table_name="literature_files")
    existing = {c["name"] for c in insp.get_columns("literature_files")}
    if "content_sha256" in existing:
        op.drop_column("literature_files", "content_sha256")
