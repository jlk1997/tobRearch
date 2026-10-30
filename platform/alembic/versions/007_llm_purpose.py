"""LLM credential kind + embed/rerank model names.

Revision ID: 007_llm_purpose
Revises: 006_reading_progress
Create Date: 2026-09-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "007_llm_purpose"
down_revision = "006_reading_progress"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, col: str) -> bool:
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == col for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if not _has_column(insp, "llm_credentials", "kind"):
        op.add_column("llm_credentials", sa.Column("kind", sa.String(20), nullable=False, server_default="cloud"))
    if not _has_column(insp, "llm_credentials", "model_embed"):
        op.add_column("llm_credentials", sa.Column("model_embed", sa.String(200), nullable=False, server_default=""))
    if not _has_column(insp, "llm_credentials", "model_rerank"):
        op.add_column("llm_credentials", sa.Column("model_rerank", sa.String(200), nullable=False, server_default=""))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    for col in ("model_rerank", "model_embed", "kind"):
        if _has_column(insp, "llm_credentials", col):
            op.drop_column("llm_credentials", col)
