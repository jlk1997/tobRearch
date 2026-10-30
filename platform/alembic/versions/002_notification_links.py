"""Add notification deep-link columns if missing.

Revision ID: 002_notification_links
Revises: 001_initial
Create Date: 2026-09-05
"""

from alembic import op
from sqlalchemy import Column, String, inspect

revision = "002_notification_links"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "notifications" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("notifications")}
    if "object_type" not in cols:
        op.add_column("notifications", Column("object_type", String(40), server_default=""))
    if "object_id" not in cols:
        op.add_column("notifications", Column("object_id", String(36), server_default=""))
    if "lab_id" not in cols:
        op.add_column("notifications", Column("lab_id", String(36), server_default=""))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "notifications" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("notifications")}
    if "lab_id" in cols:
        op.drop_column("notifications", "lab_id")
    if "object_id" in cols:
        op.drop_column("notifications", "object_id")
    if "object_type" in cols:
        op.drop_column("notifications", "object_type")
