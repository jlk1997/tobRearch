"""A1/A2/A3/A9：页文本与页译文缓存、结构树、锚定高亮、辅导复合索引。

页表与译文表都是缓存，可清空重建，不是业务事实来源。

Revision ID: 017_literature_page_cache
Revises: 016_metric_daily
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "017_literature_page_cache"
down_revision = "016_metric_daily"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "literature_files" in tables:
        cols = {c["name"] for c in insp.get_columns("literature_files")}
        if "text_hash" not in cols:
            op.add_column("literature_files", sa.Column("text_hash", sa.String(64), nullable=True))
        if "structure_json" not in cols:
            op.add_column("literature_files", sa.Column("structure_json", sa.Text(), nullable=True))

    if "literature_notes" in tables:
        cols = {c["name"] for c in insp.get_columns("literature_notes")}
        if "anchor_json" not in cols:
            op.add_column("literature_notes", sa.Column("anchor_json", sa.Text(), nullable=True))

    if "literature_analyses" in tables:
        names = {i["name"] for i in insp.get_indexes("literature_analyses")}
        if "ix_literature_analyses_item_type_by" not in names:
            op.create_index(
                "ix_literature_analyses_item_type_by",
                "literature_analyses",
                ["item_id", "analysis_type", "created_by"],
            )

    if "literature_file_pages" not in tables:
        op.create_table(
            "literature_file_pages",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("file_id", sa.String(36), sa.ForeignKey("literature_files.id"), nullable=False, index=True),
            sa.Column("item_id", sa.String(36), sa.ForeignKey("literature_items.id"), nullable=False, index=True),
            sa.Column("page_no", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False, server_default=""),
            sa.UniqueConstraint("file_id", "page_no"),
        )
        op.create_index("ix_literature_file_pages_item", "literature_file_pages", ["item_id", "page_no"])

    if "literature_page_translations" not in tables:
        op.create_table(
            "literature_page_translations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("item_id", sa.String(36), sa.ForeignKey("literature_items.id"), nullable=False, index=True),
            sa.Column("page_no", sa.Integer(), nullable=False),
            sa.Column("lang", sa.String(16), nullable=False, server_default="zh"),
            sa.Column("text_hash", sa.String(64), nullable=False, server_default=""),
            sa.Column("text", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("item_id", "page_no", "lang"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "literature_page_translations" in tables:
        op.drop_table("literature_page_translations")
    if "literature_file_pages" in tables:
        op.drop_index("ix_literature_file_pages_item", table_name="literature_file_pages")
        op.drop_table("literature_file_pages")
    if "literature_analyses" in tables:
        names = {i["name"] for i in insp.get_indexes("literature_analyses")}
        if "ix_literature_analyses_item_type_by" in names:
            op.drop_index("ix_literature_analyses_item_type_by", table_name="literature_analyses")
    if "literature_notes" in tables:
        cols = {c["name"] for c in insp.get_columns("literature_notes")}
        if "anchor_json" in cols:
            op.drop_column("literature_notes", "anchor_json")
    if "literature_files" in tables:
        cols = {c["name"] for c in insp.get_columns("literature_files")}
        if "structure_json" in cols:
            op.drop_column("literature_files", "structure_json")
        if "text_hash" in cols:
            op.drop_column("literature_files", "text_hash")
