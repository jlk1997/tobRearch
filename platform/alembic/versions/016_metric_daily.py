"""Daily lab metric rollup for conversion trends.

Revision ID: 016_metric_daily
Revises: 015_kb_direction
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "016_metric_daily"
down_revision = "015_kb_direction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "lab_metric_daily" in insp.get_table_names():
        return
    op.create_table(
        "lab_metric_daily",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lab_id", sa.String(36), sa.ForeignKey("labs.id"), nullable=False, index=True),
        sa.Column("direction_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("user_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("day", sa.String(10), nullable=False, index=True),
        sa.Column("metrics_json", sa.Text(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("lab_id", "direction_id", "user_id", "day"),
    )
    op.create_index("ix_lab_metric_daily_lab_day", "lab_metric_daily", ["lab_id", "day"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "lab_metric_daily" not in insp.get_table_names():
        return
    op.drop_index("ix_lab_metric_daily_lab_day", table_name="lab_metric_daily")
    op.drop_table("lab_metric_daily")
