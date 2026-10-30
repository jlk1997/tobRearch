"""A-PROFILE：成员档案加列（不建影子表）+ 入门语料 source_kind。

档案渲染在现有成员页与进度成员卡；语料复用现有 pgvector 索引。

Revision ID: 019_member_profile
Revises: 018_agent_traces
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "019_member_profile"
down_revision = "018_agent_traces"
branch_labels = None
depends_on = None

PROFILE_COLUMNS = [
    ("bio", sa.Text(), ""),
    ("interests_json", sa.Text(), "[]"),
    ("skills_json", sa.Text(), "[]"),
    ("homepage", sa.String(300), ""),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "lab_memberships" in tables:
        cols = {c["name"] for c in insp.get_columns("lab_memberships")}
        for name, typ, default in PROFILE_COLUMNS:
            if name not in cols:
                op.add_column("lab_memberships", sa.Column(name, typ, nullable=False, server_default=default))
        if "advisor_user_id" not in cols:
            op.add_column("lab_memberships", sa.Column("advisor_user_id", sa.String(36), nullable=True))
        for name in ("enrolled_at", "expected_graduate_at"):
            if name not in cols:
                op.add_column("lab_memberships", sa.Column(name, sa.DateTime(timezone=True), nullable=True))

    if "kb_documents" in tables:
        cols = {c["name"] for c in insp.get_columns("kb_documents")}
        if "source_kind" not in cols:
            op.add_column(
                "kb_documents",
                sa.Column("source_kind", sa.String(24), nullable=False, server_default="paper"),
            )
        names = {i["name"] for i in insp.get_indexes("kb_documents")}
        if "ix_kb_documents_kind" not in names:
            op.create_index("ix_kb_documents_kind", "kb_documents", ["kb_id", "source_kind"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "kb_documents" in tables:
        names = {i["name"] for i in insp.get_indexes("kb_documents")}
        if "ix_kb_documents_kind" in names:
            op.drop_index("ix_kb_documents_kind", table_name="kb_documents")
        cols = {c["name"] for c in insp.get_columns("kb_documents")}
        if "source_kind" in cols:
            op.drop_column("kb_documents", "source_kind")

    if "lab_memberships" in tables:
        cols = {c["name"] for c in insp.get_columns("lab_memberships")}
        for name in ("expected_graduate_at", "enrolled_at", "advisor_user_id"):
            if name in cols:
                op.drop_column("lab_memberships", name)
        for name, _typ, _default in reversed(PROFILE_COLUMNS):
            if name in cols:
                op.drop_column("lab_memberships", name)
