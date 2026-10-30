"""Notification payload for comment/line deep links.

Revision ID: 010_notification_payload
Revises: 009_minutes_title
Create Date: 2026-09-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "010_notification_payload"
down_revision = "009_minutes_title"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "notifications" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("notifications")}
    if "payload_json" not in cols:
        op.add_column("notifications", sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "notifications" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("notifications")}
    if "payload_json" in cols:
        op.drop_column("notifications", "payload_json")
