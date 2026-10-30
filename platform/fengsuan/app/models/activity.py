from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fengsuan.app.db.base import Base, TimestampMixin, UUIDPrimaryKey, utcnow


class ActivityEvent(Base, UUIDPrimaryKey):
    __tablename__ = "activity_events"
    __table_args__ = (
        Index("ix_activity_lab_actor_created", "lab_id", "actor_id", "created_at"),
        Index("ix_activity_lab_created", "lab_id", "created_at"),
    )

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    verb: Mapped[str] = mapped_column(String(80), index=True)
    object_type: Mapped[str] = mapped_column(String(40), default="")
    object_id: Mapped[str] = mapped_column(String(36), default="")
    summary: Mapped[str] = mapped_column(String(500), default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class WorkLog(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "work_logs"

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    log_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content: Mapped[str] = mapped_column(Text, default="")


class LabTask(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "lab_tasks"

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="todo")
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class AuditLog(Base, UUIDPrimaryKey):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_action_created", "action", "created_at"),)

    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    resource: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(36), default="")
    ip: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LabMetricDaily(Base, UUIDPrimaryKey):
    """每日状态类指标一行一个 scope。direction_id/user_id 空串表示实验室合计。"""

    __tablename__ = "lab_metric_daily"
    __table_args__ = (
        UniqueConstraint("lab_id", "direction_id", "user_id", "day"),
        Index("ix_lab_metric_daily_lab_day", "lab_id", "day"),
    )

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    direction_id: Mapped[str] = mapped_column(String(36), default="")
    user_id: Mapped[str] = mapped_column(String(36), default="")
    day: Mapped[str] = mapped_column(String(10), index=True)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")


class Notification(Base, UUIDPrimaryKey):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_unread", "user_id", "is_read"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(40), default="info")
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    object_type: Mapped[str] = mapped_column(String(40), default="")
    object_id: Mapped[str] = mapped_column(String(36), default="")
    lab_id: Mapped[str] = mapped_column(String(36), default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
