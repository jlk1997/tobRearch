"""Store last PDF page for in-progress reading.

Revision ID: 006_reading_progress
Revises: 005_h15_landing
Create Date: 2026-09-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "006_reading_progress"
down_revision = "005_h15_landing"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, col: str) -> bool:
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == col for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if _has_column(insp, "literature_readings", "last_page_no"):
        return
    op.add_column("literature_readings", sa.Column("last_page_no", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if not _has_column(insp, "literature_readings", "last_page_no"):
        return
    op.drop_column("literature_readings", "last_page_no")
