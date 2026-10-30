"""Action source_line + membership title.

Revision ID: 009_minutes_title
Revises: 008_degree_milestones
Create Date: 2026-09-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "009_minutes_title"
down_revision = "008_degree_milestones"
branch_labels = None
depends_on = None


def _cols(insp, table: str) -> set[str]:
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "source_line" not in _cols(insp, "action_items"):
        op.add_column("action_items", sa.Column("source_line", sa.Integer(), nullable=True))
    if "title" not in _cols(insp, "lab_memberships"):
        op.add_column(
            "lab_memberships",
            sa.Column("title", sa.String(80), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "title" in _cols(insp, "lab_memberships"):
        op.drop_column("lab_memberships", "title")
    if "source_line" in _cols(insp, "action_items"):
        op.drop_column("action_items", "source_line")
