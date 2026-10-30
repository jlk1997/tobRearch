"""Performance indexes for cockpit, notifications, and audit.

Revision ID: 003_perf_indexes
Revises: 002_notification_links
Create Date: 2026-09-05
"""

from alembic import op
from sqlalchemy import inspect

revision = "003_perf_indexes"
down_revision = "002_notification_links"
branch_labels = None
depends_on = None


def _has_index(insp, table: str, name: str) -> bool:
    if table not in insp.get_table_names():
        return True
    return any(i["name"] == name for i in insp.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if not _has_index(insp, "activity_events", "ix_activity_lab_actor_created"):
        op.create_index("ix_activity_lab_actor_created", "activity_events", ["lab_id", "actor_id", "created_at"])
    if not _has_index(insp, "notifications", "ix_notifications_user_unread"):
        op.create_index("ix_notifications_user_unread", "notifications", ["user_id", "is_read"])
    if not _has_index(insp, "audit_logs", "ix_audit_action_created"):
        op.create_index("ix_audit_action_created", "audit_logs", ["action", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if _has_index(insp, "audit_logs", "ix_audit_action_created"):
        op.drop_index("ix_audit_action_created", table_name="audit_logs")
    if _has_index(insp, "notifications", "ix_notifications_user_unread"):
        op.drop_index("ix_notifications_user_unread", table_name="notifications")
    if _has_index(insp, "activity_events", "ix_activity_lab_actor_created"):
        op.drop_index("ix_activity_lab_actor_created", table_name="activity_events")
