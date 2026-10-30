"""Job and list query indexes.

Revision ID: 004_job_indexes
Revises: 003_perf_indexes
Create Date: 2026-09-05
"""

from alembic import op
from sqlalchemy import inspect

revision = "004_job_indexes"
down_revision = "003_perf_indexes"
branch_labels = None
depends_on = None


def _has_index(insp, table: str, name: str) -> bool:
    if table not in insp.get_table_names():
        return True
    return any(i["name"] == name for i in insp.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    specs = [
        ("research_runs", "ix_research_runs_status_created", ["status", "created_at"]),
        ("reproduce_runs", "ix_reproduce_runs_status_created", ["status", "created_at"]),
        ("kb_documents", "ix_kb_documents_status_created", ["status", "created_at"]),
        ("literature_items", "ix_literature_items_lab_deleted", ["lab_id", "deleted_at"]),
        ("manuscripts", "ix_manuscripts_lab_deleted", ["lab_id", "deleted_at"]),
        ("activity_events", "ix_activity_lab_created", ["lab_id", "created_at"]),
    ]
    for table, name, cols in specs:
        if not _has_index(insp, table, name):
            op.create_index(name, table, cols)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    for table, name, _cols in (
        ("activity_events", "ix_activity_lab_created", None),
        ("manuscripts", "ix_manuscripts_lab_deleted", None),
        ("literature_items", "ix_literature_items_lab_deleted", None),
        ("kb_documents", "ix_kb_documents_status_created", None),
        ("reproduce_runs", "ix_reproduce_runs_status_created", None),
        ("research_runs", "ix_research_runs_status_created", None),
    ):
        if _has_index(insp, table, name):
            op.drop_index(name, table_name=table)
