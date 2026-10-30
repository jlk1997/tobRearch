"""A-AGENT：Agent 运行时逐步审计表。

只做审计与复算（「这个答案是怎么来的」），业务事实仍在原表。

Revision ID: 018_agent_traces
Revises: 017_literature_page_cache
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "018_agent_traces"
down_revision = "017_literature_page_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "agent_traces" in set(insp.get_table_names()):
        return
    op.create_table(
        "agent_traces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lab_id", sa.String(36), sa.ForeignKey("labs.id"), nullable=False, index=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("session_id", sa.String(64), nullable=False, server_default="", index=True),
        sa.Column("feature", sa.String(40), nullable=False, server_default=""),
        sa.Column("scope", sa.String(20), nullable=False, server_default="paper"),
        sa.Column("step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(20), nullable=False, server_default="tool"),
        sa.Column("tool", sa.String(80), nullable=False, server_default=""),
        sa.Column("args_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_traces_lab_feature", "agent_traces", ["lab_id", "feature", "created_at"])
    op.create_index("ix_agent_traces_session_step", "agent_traces", ["session_id", "step"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "agent_traces" not in set(insp.get_table_names()):
        return
    op.drop_index("ix_agent_traces_session_step", table_name="agent_traces")
    op.drop_index("ix_agent_traces_lab_feature", table_name="agent_traces")
    op.drop_table("agent_traces")
