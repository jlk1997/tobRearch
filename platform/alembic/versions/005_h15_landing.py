"""H1.5 landing tables and pgvector.

Revision ID: 005_h15_landing
Revises: 004_job_indexes
Create Date: 2026-09-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "005_h15_landing"
down_revision = "004_job_indexes"
branch_labels = None
depends_on = None


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def _has_column(insp, table: str, col: str) -> bool:
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == col for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if not _has_table(insp, "datasets"):
        op.create_table(
            "datasets",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("lab_id", sa.String(36), sa.ForeignKey("labs.id"), index=True, nullable=False),
            sa.Column("name", sa.String(300), nullable=False),
            sa.Column("description", sa.Text(), server_default=""),
            sa.Column("source_note", sa.Text(), server_default=""),
            sa.Column("license", sa.String(120), server_default=""),
            sa.Column("local_path", sa.String(1000), server_default=""),
            sa.Column("file_path", sa.String(1000), server_default=""),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_table(insp, "dataset_literature_links"):
        op.create_table(
            "dataset_literature_links",
            sa.Column("dataset_id", sa.String(36), sa.ForeignKey("datasets.id"), primary_key=True),
            sa.Column("literature_item_id", sa.String(36), sa.ForeignKey("literature_items.id"), primary_key=True),
        )
    if _has_table(insp, "literature_notes") and not _has_column(insp, "literature_notes", "page_no"):
        op.add_column("literature_notes", sa.Column("page_no", sa.Integer(), nullable=True))
    # SQLite cannot ALTER ADD COLUMN with inline ForeignKey; add plain column.
    if _has_table(insp, "research_runs") and not _has_column(insp, "research_runs", "dataset_id"):
        op.add_column("research_runs", sa.Column("dataset_id", sa.String(36), nullable=True))
    if _has_table(insp, "reproduce_runs") and not _has_column(insp, "reproduce_runs", "dataset_id"):
        op.add_column("reproduce_runs", sa.Column("dataset_id", sa.String(36), nullable=True))
    if bind.dialect.name == "postgresql":
        try:
            bind.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            if _has_table(insp, "kb_chunks") and not _has_column(insp, "kb_chunks", "embedding"):
                bind.execute(text("ALTER TABLE kb_chunks ADD COLUMN embedding vector(1024)"))
        except Exception:
            pass


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if _has_column(insp, "reproduce_runs", "dataset_id"):
        op.drop_column("reproduce_runs", "dataset_id")
    if _has_column(insp, "research_runs", "dataset_id"):
        op.drop_column("research_runs", "dataset_id")
    if _has_column(insp, "literature_notes", "page_no"):
        op.drop_column("literature_notes", "page_no")
    if _has_table(insp, "dataset_literature_links"):
        op.drop_table("dataset_literature_links")
    if _has_table(insp, "datasets"):
        op.drop_table("datasets")
